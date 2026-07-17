from django.urls import path

from . import views


urlpatterns = [
    path("_assets/editor/<str:name>", views.runtime_asset, name="runtime_asset"),
    path("", views.dashboard, name="dashboard"),
    path("rozmowy/nowa/", views.start_session, name="start_session"),
    path("strony/<int:site_id>/podpowiedzi-url/", views.site_url_suggestions, name="site_url_suggestions"),
    path("rozmowy/<uuid:session_id>/", views.session_detail, name="session_detail"),
    path("rozmowy/<uuid:session_id>/postep/", views.session_progress, name="session_progress"),
    path("rozmowy/<uuid:session_id>/strony/<uuid:conversation_id>/", views.session_detail, name="page_conversation"),
    path("rozmowy/<uuid:session_id>/strony/otworz/", views.navigate_page, name="navigate_page"),
    path("rozmowy/<uuid:session_id>/strony/<uuid:conversation_id>/wiadomosc/", views.send_message, name="send_message"),
    path("rozmowy/<uuid:session_id>/zalaczniki/<int:message_id>/<str:variant>/", views.message_attachment, name="message_attachment"),
    path("rozmowy/<uuid:session_id>/strony/<uuid:conversation_id>/edytuj-tekst/", views.inline_edit, name="inline_edit"),
    path("rozmowy/<uuid:session_id>/przywroc/", views.reset_session, name="reset_session"),
    path("rozmowy/<uuid:session_id>/publikuj/", views.publish_session, name="publish_session"),
    path("rozmowy/<uuid:session_id>/usun/", views.delete_session, name="delete_session"),
    path("wewnetrzne/podglad/<uuid:session_id>/autoryzuj/", views.preview_authorize, name="preview_authorize"),
]
