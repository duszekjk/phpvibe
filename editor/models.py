import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse


class Site(models.Model):
    name = models.CharField("nazwa", max_length=120)
    slug = models.SlugField(unique=True)
    config_key = models.SlugField(
        "plik konfiguracji strony",
        unique=True,
        help_text=(
            "Nazwa pliku TOML bez rozszerzenia. Przykładowo wartość "
            "„jerozolima” oznacza plik site_configs/jerozolima.toml."
        ),
    )
    is_active = models.BooleanField("aktywna", default=True)
    users = models.ManyToManyField(settings.AUTH_USER_MODEL, through="SiteMembership", related_name="editable_sites")

    class Meta:
        ordering = ["name"]
        verbose_name = "strona"
        verbose_name_plural = "strony"

    def __str__(self):
        return self.name


class SiteMembership(models.Model):
    class Role(models.TextChoices):
        EDITOR = "editor", "Edytor"
        PUBLISHER = "publisher", "Edytor i publikujący"

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="site_memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EDITOR)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["site", "user"], name="unique_site_user")]

    def __str__(self):
        return f"{self.user} – {self.site} ({self.get_role_display()})"


class EditSession(models.Model):
    class Status(models.TextChoices):
        PREPARING = "preparing", "Przygotowywanie"
        ACTIVE = "active", "Aktywna"
        PUBLISHED = "published", "Opublikowana"
        FAILED = "failed", "Błąd"
        ARCHIVED = "archived", "Zarchiwizowana"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="edit_sessions")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="edit_sessions")
    title = models.CharField("temat rozmowy", max_length=180)
    target_url = models.URLField("adres edytowanej strony", max_length=1000)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PREPARING)
    workspace_path = models.TextField(blank=True)
    baseline_commit = models.CharField(max_length=64, blank=True)
    baseline_manifest = models.JSONField(default=dict, blank=True)
    preview_transforms = models.JSONField(default=list, blank=True)
    last_response_id = models.CharField(max_length=200, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("session_detail", kwargs={"session_id": self.pk})


class PageConversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(EditSession, on_delete=models.CASCADE, related_name="conversations")
    target_url = models.URLField("adres podstrony", max_length=1000)
    normalized_url = models.CharField(max_length=1000)
    label = models.CharField(max_length=180, blank=True)
    last_response_id = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["session", "normalized_url"], name="unique_session_page_url")
        ]

    def __str__(self):
        return self.label or self.target_url

    def get_absolute_url(self):
        return reverse(
            "page_conversation",
            kwargs={"session_id": self.session_id, "conversation_id": self.pk},
        )


class ChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", "Użytkownik"
        ASSISTANT = "assistant", "Asystent"
        SYSTEM = "system", "System"

    session = models.ForeignKey(EditSession, on_delete=models.CASCADE, related_name="messages")
    conversation = models.ForeignKey(
        PageConversation,
        on_delete=models.CASCADE,
        related_name="messages",
        null=True,
        blank=True,
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    content = models.TextField()
    context = models.JSONField(default=dict, blank=True)
    response_id = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]


class Revision(models.Model):
    session = models.ForeignKey(EditSession, on_delete=models.CASCADE, related_name="revisions")
    commit_hash = models.CharField(max_length=64)
    summary = models.CharField(max_length=240)
    changed_files = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
