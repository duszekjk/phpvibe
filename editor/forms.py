from django import forms
from django.core.exceptions import ValidationError

from .config import load_site_config
from .models import EditSession, Site


class StartSessionForm(forms.ModelForm):
    class Meta:
        model = EditSession
        fields = ("site", "title", "target_url")
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Np. Aktualizacja informacji o spotkaniu"}),
            "target_url": forms.TextInput(attrs={
                "placeholder": "Wpisz nazwę podstrony albo wklej pełny adres URL",
                "autocomplete": "off",
                "inputmode": "url",
            }),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        sites = Site.objects.filter(is_active=True, memberships__user=user).distinct()
        self.fields["site"].queryset = sites
        if not self.is_bound:
            first_site = sites.first()
            if first_site:
                self.initial.setdefault("site", first_site.pk)

    def clean(self):
        from .navigation import normalize_page_url

        data = super().clean()
        site = data.get("site")
        target_url = data.get("target_url")
        if not site or not target_url:
            return data
        config = load_site_config(site.config_key)
        try:
            data["target_url"] = normalize_page_url(target_url, config.allowed_hosts)
        except ValidationError as exc:
            self.add_error("target_url", exc)
        return data


class ChatForm(forms.Form):
    message = forms.CharField(
        label="",
        max_length=20_000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Opisz zmianę zwykłymi słowami…"}),
    )
    image = forms.FileField(
        label="Dodaj zdjęcie",
        required=False,
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp,image/gif"}),
    )

    def clean(self):
        data = super().clean()
        if not data.get("message", "").strip() and not data.get("image"):
            raise ValidationError("Napisz wiadomość albo dodaj zdjęcie.")
        return data


class PageAddressForm(forms.Form):
    url = forms.URLField(label="Adres podstrony", max_length=1000)

    def __init__(self, *args, edit_session, **kwargs):
        super().__init__(*args, **kwargs)
        self.edit_session = edit_session

    def clean_url(self):
        from .navigation import normalize_page_url

        config = load_site_config(self.edit_session.site.config_key)
        return normalize_page_url(self.cleaned_data["url"], config.allowed_hosts)


class InlineEditForm(forms.Form):
    old_text = forms.CharField(max_length=10_000)
    new_text = forms.CharField(max_length=10_000)
    selector = forms.CharField(max_length=2_000, required=False)
    tag_name = forms.CharField(max_length=80, required=False)
    outer_html = forms.CharField(max_length=8_000, required=False)

    def clean(self):
        data = super().clean()
        old_text = data.get("old_text", "").strip()
        new_text = data.get("new_text", "").strip()
        if not old_text:
            self.add_error("old_text", "Nie znaleziono tekstu elementu.")
        if old_text == new_text:
            self.add_error("new_text", "Nowy tekst jest taki sam jak obecny.")
        return data
