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
- endpoint autoryzacyjny oraz helper `RewriteMap` chroniący podgląd obsługiwany przez Apache.

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
- `VIBE_PANEL_ORIGIN` — dokładny origin panelu, domyślnie `https://phpvibe.duszekjk.com`;
- `OPENAI_API_KEY` — klucz przechowywany wyłącznie po stronie serwera;
- `OPENAI_MODEL` — domyślnie `gpt-5.6-terra`;
- `OPENAI_MAX_TOOL_ROUNDS` i `VIBE_FILE_MAX_BYTES`;
- `VIBE_PREVIEW_TOKEN_MAX_AGE` — czas ważności tokenu podglądu, domyślnie 8 godzin.

## Konfiguracja strony

Punktem wyjścia jest [jerozolima.toml.example](site_configs/jerozolima.toml.example). Opis struktury jest zaufaną instrukcją dla modelu. Warto podać w nim regułę mapowania parametrów URL na pliki, położenie treści, stylów i elementów wspólnych.

`allowed_extensions` ogranicza pliki widoczne dla asystenta. `protected_paths` przyjmuje wzorce glob. Pasujące pliki i katalogi nie są kopiowane do wykonywalnej kopii podglądu i nie są dostępne dla narzędzi AI. Jeżeli podgląd potrzebuje konfiguracji zastępczej, trzeba dodać do kopii osobny, pozbawiony sekretów plik testowy.

Po każdej zmianie TOML uruchom ponownie procesy aplikacji (konfiguracje są cache'owane) oraz:

```bash
python manage.py sync_site_configs
```

## Podgląd PHP — wymagany model bezpieczeństwa

Nie należy uruchamiać kodu PHP zmienianego przez model w głównym poolu PHP-FPM. Podgląd musi działać w osobnym kontenerze albo przynajmniej w osobnym poolu pod nieuprzywilejowanym użytkownikiem, z:

- katalogiem roboczym konkretnego projektu edycyjnego jako jedynym katalogiem strony;
- prawami systemowymi tylko do odczytu dla użytkownika procesu PHP (pliki zmienia wyłącznie proces Django);
- `open_basedir` ograniczonym do tej kopii i bez sekretów produkcyjnych;
- wyłączonymi funkcjami systemowymi (`exec`, `shell_exec`, `system`, `passthru`, `proc_open`, `popen`);
- brakiem dostępu do bazy produkcyjnej i, o ile strona tego nie wymaga, brakiem wyjścia do sieci;
- limitami CPU, pamięci i czasu wykonania;
- autoryzacją każdego żądania przez endpoint `/wewnetrzne/podglad/<uuid>/autoryzuj/`.

W kopii Jerozolimy są skrypty używające `file_put_contents`, dlatego rozdzielenie użytkownika Django od użytkownika PHP i odebranie temu drugiemu prawa zapisu jest wymaganiem, nie opcjonalnym utwardzeniem. Dla katalogu podglądu ustaw także `AllowOverride None`, aby produkcyjny `.htaccess` skopiowany razem ze stroną nie zmieniał reguł reverse proxy ani bezpieczeństwa podglądu.

`preview_url_template` wskazuje trasę Apache mapującą UUID wyłącznie na `VIBE_WORKSPACE_ROOT/<uuid>/site`. Zewnętrzna konfiguracja Apache sprawdza format UUID i przekazuje parę UUID/token do trwałego helpera `RewriteMap`. Helper pyta lokalny endpoint Django, a Apache udostępnia plik dopiero po odpowiedzi `204`. Nie wolno budować ścieżki z dowolnego fragmentu URL.

Podgląd działa na osobnym originie `https://tmp.jerozolima.org`, bez ciasteczka sesji Django. Panel dodaje do adresu krótko ważny, podpisany parametr `__vibe_token`, ograniczony do użytkownika i konkretnej kopii. Helper Apache przekazuje go do:

```text
/wewnetrzne/podglad/<session_id>/autoryzuj/?token=<__vibe_token>
```

Przy pierwszym poprawnym żądaniu Apache zapisuje token jako cookie `Secure; HttpOnly; SameSite=Lax`, ograniczone ścieżką do `/vibe/<session_id>/`. Kolejne żądania obrazów, CSS, JS i PHP są sprawdzane z tym cookie. Nie wolno współdzielić z `tmp.jerozolima.org` ciasteczka sesji panelu Django.

Podczas tworzenia kopii aplikacja dodaje chroniony katalog `__phpvibe_preview/` z JS i CSS edytora. Dla Jerozolimy druga transformacja `preview_replacements` zamienia generowane przez PHP `<head>` na:

```html
<head>
<link rel="stylesheet" href="__phpvibe_preview/preview.css">
<script defer src="__phpvibe_preview/preview-bridge.js"></script>
```

Bridge przechwytuje wybór tekstu i wewnętrzną nawigację, a następnie komunikuje się z panelem przez `postMessage`. Wysyła dane wyłącznie do `VIBE_PANEL_ORIGIN` i przyjmuje polecenia wyłącznie z tego originu. Apache ustawia CSP `frame-ancestors` dopuszczające tylko panel.

### Wdrożenie Apache i Certbot

Konfiguracja vhostów panelu i podglądu dla portów `80`, `8080` i `443` jest zależna od konkretnego serwera i celowo nie jest przechowywana w tym repozytorium. To samo dotyczy pliku LaunchAgent `plist`. Pliki te należy utrzymywać poza katalogiem projektu. Ścieżki `/.well-known/acme-challenge/` i `/static/` muszą być obsługiwane przed proxy.

Główna konfiguracja Apache musi już zawierać `Listen 8080` oraz `Listen 443` i mieć załadowane moduły `rewrite`, `proxy`, `proxy_http`, `headers`, `ssl`, `alias`, `dir`, `actions` oraz odpowiednio `cgi` (MPM prefork) albo `cgid` (MPM event/worker). Podgląd nie dziedziczy nieznanego handlera PHP z innego vhosta. Dedykowany launcher `php_preview_cgi.py` uruchamia `php-cgi`, sprawdza zgodność URL-u ze ścieżką kopii i ustawia osobny `open_basedir` dla UUID. Brak `php-cgi` kończy się odpowiedzią `503`, nigdy wysłaniem źródła PHP.

Po sklonowaniu repozytorium do `/private/var/www/phpvibe`:

```bash
chmod 755 /private/var/www/phpvibe/deploy/preview_auth_map.py \
  /private/var/www/phpvibe/deploy/php_preview_cgi.py
command -v php-cgi
sudo mkdir -p /private/var/www/certbot/.well-known/acme-challenge
sudo chown -R _www:_www /private/var/www/certbot
sudo apachectl -t
sudo apachectl graceful
sudo certbot certonly --webroot -w /private/var/www/certbot \
  -d phpvibe.duszekjk.com -d tmp.jerozolima.org
sudo apachectl -t
sudo apachectl graceful
```

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
