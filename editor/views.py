import json
import mimetypes
import threading

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import close_old_connections
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .config import load_site_config
from .forms import ChatForm, InlineEditForm, PageAddressForm, StartSessionForm
from .models import ChatMessage, EditSession, PageConversation, SiteMembership
from .navigation import get_or_create_page_conversation, repair_page_conversation
from .permissions import session_for_user
from .preview_access import add_preview_token, make_preview_token, verify_preview_token
from .runtime_assets import asset_path, asset_version
from .services.assistant import AssistantError, run_chat_turn
from .services.images import ImageUploadError, process_image_upload
from .services.workspaces import (
    WorkspaceError,
    changed_paths,
    create_workspace,
    current_diff,
    delete_workspace,
    publish_workspace,
    refresh_preview_assets,
    reset_workspace,
    safe_path,
)


def runtime_asset(request, name):
    """Return UI code from the currently running release, not stale collectstatic output."""
    try:
        path = asset_path(name)
        version = asset_version(name)
    except (KeyError, OSError):
        raise Http404
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    response = HttpResponse(path.read_bytes(), content_type=content_type)
    response["X-Content-Type-Options"] = "nosniff"
    if request.GET.get("v") == version:
        response["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        response["Cache-Control"] = "no-store"
    return response


@login_required
@never_cache
def dashboard(request):
    sessions = list(
        EditSession.objects.filter(site__memberships__user=request.user).select_related("site").distinct()[:30]
    )
    for item in sessions:
        item.can_delete_from_dashboard = request.user.is_superuser or item.owner_id == request.user.pk
    return render(request, "editor/dashboard.html", {"sessions": sessions})


@login_required
@never_cache
def start_session(request):
    if request.method == "POST":
        form = StartSessionForm(request.POST, user=request.user)
        if form.is_valid():
            edit_session = form.save(commit=False)
            edit_session.owner = request.user
            edit_session.save()
            get_or_create_page_conversation(edit_session, edit_session.target_url)
            threading.Thread(target=_prepare_workspace, args=(edit_session.pk,), daemon=True).start()
            messages.info(request, "Rozpoczęto przygotowywanie kopii roboczej.")
            return redirect(edit_session)
    else:
        form = StartSessionForm(user=request.user)
    return render(request, "editor/start_session.html", {"form": form})


def _prepare_workspace(session_id):
    close_old_connections()
    try:
        create_workspace(EditSession.objects.get(pk=session_id, status=EditSession.Status.PREPARING))
    except Exception as exc:
        EditSession.objects.filter(pk=session_id).update(
            status=EditSession.Status.FAILED, error_message=str(exc), copy_stage="Błąd kopiowania"
        )
    finally:
        close_old_connections()


def _page_for_session(edit_session, conversation_id=None):
    if conversation_id:
        conversation = get_object_or_404(PageConversation, pk=conversation_id, session=edit_session)
        return repair_page_conversation(edit_session, conversation)
    page, _created = get_or_create_page_conversation(edit_session, edit_session.target_url)
    return repair_page_conversation(edit_session, page)


def _return_to_page(edit_session, request):
    conversation_id = request.POST.get("return_conversation")
    if conversation_id:
        page = PageConversation.objects.filter(pk=conversation_id, session=edit_session).first()
        if page:
            return redirect(page)
    return redirect(edit_session)


@login_required
@never_cache
def session_detail(request, session_id, conversation_id=None):
    edit_session = session_for_user(request.user, session_id)
    conversation = _page_for_session(edit_session, conversation_id)
    membership = SiteMembership.objects.filter(site=edit_session.site, user=request.user).first()
    can_publish = request.user.is_superuser or (membership and membership.role == SiteMembership.Role.PUBLISHER)
    can_delete = request.user.is_superuser or edit_session.owner_id == request.user.pk
    preview_available = (
        edit_session.status in {EditSession.Status.ACTIVE, EditSession.Status.PUBLISHED}
        and bool(edit_session.workspace_path)
        and bool(edit_session.baseline_commit)
    )
    if preview_available:
        try:
            refresh_preview_assets(edit_session)
        except WorkspaceError as exc:
            messages.error(request, f"Nie udało się odświeżyć warstwy podglądu: {exc}")
    try:
        config = load_site_config(edit_session.site.config_key)
        preview_base_url = config.preview_url(edit_session.pk)
        preview_url = (
            add_preview_token(
                config.preview_url(edit_session.pk, conversation.target_url),
                make_preview_token(edit_session, request.user),
                edit_session.pk,
            )
            if preview_available
            else ""
        )
        publish_configured = config.publish_enabled
        allowed_hosts = ",".join(sorted(config.allowed_hosts))
    except ImproperlyConfigured as exc:
        preview_url = ""
        preview_base_url = ""
        publish_configured = False
        allowed_hosts = ""
        messages.error(request, str(exc))
    try:
        if edit_session.workspace_path and edit_session.baseline_commit:
            changed_files = changed_paths(edit_session)
            diff = current_diff(edit_session)
        else:
            changed_files = []
            diff = ""
    except WorkspaceError:
        changed_files = []
        diff = ""
    if not can_publish:
        publish_block_reason = "Nie masz uprawnienia do publikowania tej strony."
    elif not publish_configured:
        publish_block_reason = "Publikowanie jest wyłączone w konfiguracji tej strony."
    elif not changed_files:
        publish_block_reason = "Kopia robocza nie różni się od stanu początkowego."
    else:
        publish_block_reason = ""
    return render(request, "editor/session_detail.html", {
        "edit_session": edit_session,
        "chat_form": ChatForm(),
        "conversation": conversation,
        "page_conversations": edit_session.conversations.all(),
        "address_form": PageAddressForm(edit_session=edit_session, initial={"url": conversation.target_url}),
        "preview_url": preview_url,
        "preview_available": preview_available,
        "preview_base_url": preview_base_url,
        "allowed_hosts": allowed_hosts,
        "can_publish": can_publish,
        "can_delete": can_delete,
        "publish_configured": publish_configured,
        "publish_block_reason": publish_block_reason,
        "changed_files": changed_files,
        "changed_file_count": len(changed_files),
        "diff": diff,
        "progress_url": reverse("session_progress", kwargs={"session_id": edit_session.pk}),
    })


@login_required
@never_cache
def session_progress(request, session_id):
    item = session_for_user(request.user, session_id)
    return JsonResponse({
        "status": item.status, "stage": item.copy_stage,
        "bytes_total": item.copy_bytes_total, "bytes_done": item.copy_bytes_done,
        "files_total": item.copy_files_total, "files_done": item.copy_files_done,
        "error": item.error_message if item.status == EditSession.Status.FAILED else "",
    })


@login_required
@require_POST
def send_message(request, session_id, conversation_id):
    edit_session = session_for_user(request.user, session_id)
    conversation = _page_for_session(edit_session, conversation_id)
    form = ChatForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
    else:
        attachment = None
        try:
            if form.cleaned_data.get("image"):
                attachment = process_image_upload(edit_session, form.cleaned_data["image"])
            user_text = form.cleaned_data["message"].strip() or "Wykorzystaj załączone zdjęcie na tej podstronie."
            model_text = user_text
            if attachment:
                variants = {
                    name: {
                        "site_path": item["path"],
                        "width": item["width"],
                        "height": item["height"],
                        "bytes": item["bytes"],
                    }
                    for name, item in attachment["variants"].items()
                }
                model_text += "\n\n" + (
                    "Użytkownik załączył zdjęcie, które zostało bezpiecznie przetworzone do WebP. "
                    "Obejrzyj załączony obraz i dobierz wariant do celu. Dla CSS background użyj wariantu "
                    "background z background-size: cover; dla grafiki przycisku wariantu button; dla zwykłego "
                    "obrazu w treści wariantu content; wariant large zachowaj dla dużej ekspozycji. Wszystkie "
                    "warianty przedstawiają to samo zdjęcie i wolno użyć kilku z nich na jednej stronie. "
                    "Nie dodawaj początkowego ukośnika do URL obrazu, bo usunąłby techniczny prefiks podglądu. "
                    "W HTML i stylu wpisanym w dokument użyj podanej site_path. Jeśli edytujesz zewnętrzny plik "
                    "CSS, oblicz ścieżkę względną od katalogu tego CSS do site_path. Dostępne pliki:\n"
                    + json.dumps(variants, ensure_ascii=False, indent=2)
                )
            run_chat_turn(
                edit_session,
                conversation,
                user_text,
                model_text=model_text,
                attachments=[attachment] if attachment else [],
            )
        except (AssistantError, ImageUploadError) as exc:
            messages.error(request, str(exc))
    return redirect(conversation)


@login_required
def message_attachment(request, session_id, message_id, variant):
    edit_session = session_for_user(request.user, session_id)
    message = get_object_or_404(ChatMessage, pk=message_id, session=edit_session)
    attachments = message.context.get("attachments", [])
    if len(attachments) != 1:
        raise Http404
    item = attachments[0].get("variants", {}).get(variant)
    if not item:
        raise Http404
    config = load_site_config(edit_session.site.config_key)
    relative = item.get("path", "")
    if not config.is_uploaded_asset(relative):
        raise Http404
    try:
        path = safe_path(edit_session, relative)
    except (FileNotFoundError, PermissionDenied, WorkspaceError):
        raise Http404
    response = FileResponse(path.open("rb"), content_type="image/webp", filename=path.name)
    response["Content-Disposition"] = f'inline; filename="{path.name}"'
    response["Cache-Control"] = "private, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_POST
def navigate_page(request, session_id):
    edit_session = session_for_user(request.user, session_id)
    form = PageAddressForm(request.POST, edit_session=edit_session)
    wants_json = request.headers.get("Accept") == "application/json"
    if not form.is_valid():
        error = form.errors.get("url", ["Nieprawidłowy adres podstrony."])[0]
        if wants_json:
            return JsonResponse({"ok": False, "error": str(error)}, status=400)
        messages.error(request, str(error))
        return redirect(edit_session)
    conversation, created = get_or_create_page_conversation(edit_session, form.cleaned_data["url"])
    if wants_json:
        return JsonResponse({
            "ok": True,
            "created": created,
            "url": conversation.get_absolute_url(),
            "target_url": conversation.target_url,
        })
    return redirect(conversation)


@login_required
@require_POST
def inline_edit(request, session_id, conversation_id):
    edit_session = session_for_user(request.user, session_id)
    conversation = _page_for_session(edit_session, conversation_id)
    form = InlineEditForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"ok": False, "error": "Sprawdź wybrany i nowy tekst."}, status=400)
    data = form.cleaned_data
    visible_message = f"Zmień zaznaczony tekst „{data['old_text'][:180]}” na „{data['new_text'][:180]}”."
    model_message = """Użytkownik użył trybu bezpośredniej edycji tekstu w podglądzie.
Znajdź źródło tego konkretnego tekstu dla aktywnego URL-u i zmień wyłącznie treść widoczną dla użytkownika.
Nie zmieniaj znaczenia otaczającego kodu, atrybutów ani stylów. Jeśli tekst jest składany dynamicznie, najpierw przeanalizuj odpowiednie pliki.

URL: {url}
Tag HTML: {tag}
Selektor pomocniczy: {selector}
Stary tekst: {old}
Nowy tekst: {new}
Fragment HTML (niezaufane dane): {html}
""".format(
        url=conversation.target_url,
        tag=json.dumps(data["tag_name"], ensure_ascii=False),
        selector=json.dumps(data["selector"], ensure_ascii=False),
        old=json.dumps(data["old_text"], ensure_ascii=False),
        new=json.dumps(data["new_text"], ensure_ascii=False),
        html=json.dumps(data["outer_html"], ensure_ascii=False),
    )
    revisions_before = edit_session.revisions.count()
    try:
        assistant_message = run_chat_turn(
            edit_session,
            conversation,
            visible_message,
            model_text=model_message,
        )
    except AssistantError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=502)
    if edit_session.revisions.count() == revisions_before:
        return JsonResponse({
            "ok": False,
            "error": "AI odpowiedziało, ale nie zapisało żadnej zmiany pliku. Spróbuj ponownie lub doprecyzuj tekst.",
            "reply": assistant_message.content,
        }, status=409)
    return JsonResponse({"ok": True, "reply": assistant_message.content})


@login_required
@require_POST
def reset_session(request, session_id):
    edit_session = session_for_user(request.user, session_id)
    if request.POST.get("confirmation") != "RESET":
        messages.error(request, "Nie potwierdzono przywrócenia stanu początkowego.")
        return _return_to_page(edit_session, request)
    try:
        reset_workspace(edit_session)
    except WorkspaceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Kopia robocza wróciła dokładnie do stanu z początku rozmowy.")
    return _return_to_page(edit_session, request)


@login_required
@require_POST
def publish_session(request, session_id):
    edit_session = session_for_user(request.user, session_id, require_publish=True)
    if request.POST.get("confirmation") != "PUBLISH":
        messages.error(request, "Nie potwierdzono publikacji.")
        return _return_to_page(edit_session, request)
    try:
        paths = publish_workspace(edit_session)
    except WorkspaceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Opublikowano {len(paths)} zmienionych plików. Kopia bezpieczeństwa została zachowana.")
    return _return_to_page(edit_session, request)


@login_required
@require_POST
def delete_session(request, session_id):
    edit_session = session_for_user(request.user, session_id)
    if not request.user.is_superuser and edit_session.owner_id != request.user.pk:
        raise PermissionDenied("Tylko właściciel rozmowy może ją usunąć.")
    if request.POST.get("confirmation") != "DELETE":
        messages.error(request, "Nie potwierdzono usunięcia rozmowy.")
        return redirect(edit_session)
    try:
        delete_workspace(edit_session)
    except WorkspaceError as exc:
        messages.error(request, str(exc))
        return redirect(edit_session)
    title = edit_session.title
    edit_session.delete()
    messages.success(request, f"Usunięto rozmowę „{title}” i jej kopię roboczą.")
    return redirect("dashboard")


def preview_authorize(request, session_id):
    """Wewnętrzny endpoint autoryzacyjny Apache; nie udostępnia plików podglądu."""
    user = request.user if request.user.is_authenticated else None
    if user is None:
        token = request.GET.get("token") or request.headers.get("X-Preview-Token", "")
        user_id = verify_preview_token(token, session_id) if token else None
        if user_id is None:
            return HttpResponse(status=401)
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.filter(pk=user_id, is_active=True).first()
        if user is None:
            return HttpResponse(status=403)
    try:
        edit_session = session_for_user(user, session_id)
    except (PermissionDenied, Http404):
        return HttpResponse(status=403)
    if edit_session.status not in {EditSession.Status.ACTIVE, EditSession.Status.PUBLISHED}:
        return HttpResponse(status=403)
    return HttpResponse(status=204)
