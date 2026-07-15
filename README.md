# PHP Vibe

PHP Vibe to panel Django dla nietechnicznych redaktorów starszych stron PHP. Użytkownik rozpoczyna projekt edycyjny od podania tematu i pierwszego URL-u. Aplikacja tworzy jedną odizolowaną kopię całej strony. Każdy odwiedzony URL ma osobny czat OpenAI, ale wszystkie czaty zmieniają tę samą kopię roboczą. Dopiero osobna akcja użytkownika publikuje wspólny wynik.

## Co już działa

- logowanie Django i dostęp użytkowników tylko do przypisanych stron;
- role `Edytor` oraz `Edytor i publikujący`;
- wiele stron opisanych osobnymi plikami TOML;
- walidacja, czy podany URL należy do wybranej strony;
- jedna pełna kopia plików dla projektu edycyjnego, wspólna dla wszystkich podstron;
- osobny czat i kontekst OpenAI dla każdego pełnego URL-u;
- pasek adresu i automatyczne przełączanie czatu podczas nawigacji w podglądzie;
- `Shift + klik` lub stały tryb edycji tekstu z popupem „stary tekst / nowy tekst”;
- lokalne repozytorium Git i niezmienny commit stanu początkowego;
- pełny czat zapisany w bazie oraz ciągłość rozmowy przez Responses API;
- narzędzia AI: lista plików, wyszukiwanie, odczyt, dokładna zamiana i atomowy zapis;
- blokada `../`, symlinków, niedozwolonych rozszerzeń i chronionych ścieżek;
- commit po każdej operacji edycyjnej, historia oraz dokładny diff;
- jednoznaczne przywrócenie kopii do początku rozmowy;
- publikacja tylko dla właściwej roli, z wykrywaniem zmian produkcji i kopią zapasową;
- endpoint autoryzacyjny dla podglądu chronionego przez `nginx auth_request`.

Integracja używa oficjalnego [Responses API](https://developers.openai.com/api/docs/guides/text) i [function calling](https://developers.openai.com/api/docs/guides/function-calling). Model jest ustawiany przez zmienną środowiskową, bez zaszywania go w logice aplikacji.

## Uruchomienie lokalne

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp site_configs/jerozolima.toml.example site_configs/jerozolima.toml
python manage.py migrate
python manage.py sync_site_configs
python manage.py createsuperuser
DJANGO_DEBUG=1 DJANGO_SECURE_COOKIES=0 DJANGO_SECURE_SSL_REDIRECT=0 DJANGO_SECURE_HSTS_SECONDS=0 OPENAI_API_KEY=... python manage.py runserver
```

Następnie w `/admin/` przypisz użytkownika do strony i wybierz jego rolę. Pliki `site_configs/*.toml` są celowo ignorowane przez Git, ponieważ na serwerze zawierają rzeczywiste ścieżki.

Najważniejsze zmienne:

- `DJANGO_SECRET_KEY` — obowiązkowa, długa wartość produkcyjna;
- `DJANGO_ALLOWED_HOSTS` i `DJANGO_CSRF_TRUSTED_ORIGINS`;
- `DJANGO_SECURE_SSL_REDIRECT` i `DJANGO_SECURE_HSTS_SECONDS` — wyłączaj tylko dla lokalnego HTTP;
- `VIBE_SITE_CONFIG_DIR` — katalog konfiguracji stron;
- `VIBE_WORKSPACE_ROOT` — katalog kopii rozmów;
- `OPENAI_API_KEY` — klucz przechowywany wyłącznie po stronie serwera;
- `OPENAI_MODEL` — domyślnie `gpt-5.6-terra`;
- `OPENAI_MAX_TOOL_ROUNDS` i `VIBE_FILE_MAX_BYTES`.
- `VIBE_PREVIEW_TOKEN_MAX_AGE` — czas ważności tokenu podglądu, domyślnie 8 godzin.

## Konfiguracja strony

Punktem wyjścia jest [jerozolima.toml.example](site_configs/jerozolima.toml.example). Opis struktury jest zaufaną instrukcją dla modelu. Warto podać w nim regułę mapowania parametrów URL na pliki, położenie treści, stylów i elementów wspólnych.

`allowed_extensions` ogranicza pliki widoczne dla asystenta. `protected_paths` przyjmuje wzorce glob i całkowicie ukrywa pasujące ścieżki przed narzędziami AI. Sekretów produkcyjnych najlepiej w ogóle nie kopiować do podglądu — zamiast nich podstawić bezpieczną konfigurację testową.

Po każdej zmianie TOML uruchom ponownie procesy aplikacji (konfiguracje są cache'owane) oraz:

```bash
python manage.py sync_site_configs
```

## Podgląd PHP — wymagany model bezpieczeństwa

Nie należy uruchamiać kodu PHP zmienianego przez model w głównym poolu PHP-FPM. Podgląd musi działać w osobnym kontenerze albo przynajmniej w osobnym poolu pod nieuprzywilejowanym użytkownikiem, z:

- katalogiem roboczym konkretnego projektu edycyjnego jako jedynym katalogiem strony;
- `open_basedir` ograniczonym do tej kopii i bez sekretów produkcyjnych;
- wyłączonymi funkcjami systemowymi (`exec`, `shell_exec`, `system`, `passthru`, `proc_open`, `popen`);
- brakiem dostępu do bazy produkcyjnej i, o ile strona tego nie wymaga, brakiem wyjścia do sieci;
- limitami CPU, pamięci i czasu wykonania;
- autoryzacją każdego żądania przez endpoint `/wewnetrzne/podglad/<uuid>/autoryzuj/`.

`preview_url_template` powinien wskazywać trasę reverse proxy, która mapuje UUID wyłącznie na `VIBE_WORKSPACE_ROOT/<uuid>/site`. UUID trzeba zweryfikować wyrażeniem regularnym, a przed podaniem pliku wywołać `auth_request` do Django. Nie wolno budować ścieżki z dowolnego fragmentu URL.

Podgląd działa na osobnym originie `https://tmp.jerozolima.org`, bez ciasteczka sesji Django. Panel dodaje do adresu krótko ważny, podpisany parametr `__vibe_token`, ograniczony do użytkownika i konkretnej kopii. Nginx przekazuje go do:

```text
/wewnetrzne/podglad/<session_id>/autoryzuj/?token=<__vibe_token>
```

Przy pierwszym poprawnym żądaniu Nginx powinien zapisać token jako cookie `Secure; HttpOnly; SameSite=Lax`, ograniczone ścieżką do `/vibe/<session_id>/`. Kolejne żądania obrazów, CSS, JS i PHP przekazują to cookie do `auth_request`. Nie wolno współdzielić z `tmp.jerozolima.org` ciasteczka sesji panelu Django.

Podczas tworzenia kopii aplikacja dodaje chroniony katalog `__phpvibe_preview/` z JS i CSS edytora. Strona Jerozolimy nie zamyka jawnie sekcji `head/body`, dlatego reverse proxy powinno wstrzyknąć zasoby bezpośrednio po pierwszym `<head>`:

```html
<head>
<link rel="stylesheet" href="__phpvibe_preview/preview.css">
<script defer src="__phpvibe_preview/preview-bridge.js"></script>
```

Można to zrobić np. przez `sub_filter` w Nginx po wyłączeniu kompresji odpowiedzi HTML. Bridge przechwytuje wyłącznie wybór tekstu i wewnętrzną nawigację, a następnie komunikuje się z panelem przez `postMessage`. Panel akceptuje wiadomości tylko z originu `https://tmp.jerozolima.org`. Dla podglądu ustaw również CSP `frame-ancestors` zezwalające wyłącznie na domenę panelu.

Pliki `__phpvibe_preview`, style zaznaczania i bridge są częścią wyłącznie kopii. Nie występują w produkcji, nie są widoczne dla narzędzi OpenAI i publikator odrzuci próbę ich wysłania. Konfiguracja może też zawierać `preview_replacements`, np. wyłączenie przekierowania testowej kopii `index.php` do `https://jerozolima.org`. Aplikacja zapamiętuje każdą zastosowaną transformację, odwraca ją przed publikacją i przerywa publikację, jeśli nie potrafi zrobić tego jednoznacznie.

## Publikacja i odzyskiwanie

Publikacja jest domyślnie wyłączona. Aby ją włączyć, ustaw `publish_enabled = true` i `backup_path` na katalog poza drzewem publicznym. Aplikacja:

1. pobiera wyłącznie pliki zmienione względem bazowego commita;
2. sprawdza ich hash w produkcji względem początku rozmowy;
3. przerywa przy konflikcie;
4. kopiuje stare wersje do katalogu backupu;
5. zapisuje nowe pliki przez `os.replace`;
6. rejestruje czas i stan publikacji.

Dla serwisu o wysokim ruchu kolejnym krokiem powinny być niemutowalne katalogi `releases/` i atomowe przełączanie symlinka `current`. Obecne MVP publikuje każdy zmieniony plik atomowo, ale grupa wielu plików nie jest jednym atomowym wdrożeniem.

## Ważne ograniczenia MVP

- kopiowanie dużej strony i wywołanie OpenAI są obecnie synchroniczne; produkcyjnie należy przenieść je do kolejki zadań (np. Celery/RQ) i dodać aktualizację statusu;
- nie ma jeszcze przesyłania obrazów ani usuwania plików przez model;
- SQLite służy do lokalnego startu; produkcyjnie użyj PostgreSQL;
- przed włączeniem publikacji trzeba skonfigurować i przetestować izolowany podgląd PHP;
- kopie robocze wymagają polityki retencji i cyklicznego sprzątania zarchiwizowanych projektów.

## Testy

```bash
python manage.py check
python manage.py test
```

Testy obejmują izolację ścieżek, reset Git, kontrolę hosta, konflikt produkcyjny oraz publikację z backupem.
