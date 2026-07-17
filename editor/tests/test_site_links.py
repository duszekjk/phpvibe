from django.test import SimpleTestCase

from editor.services.site_links import extract_link_suggestions


class SiteLinkExtractionTests(SimpleTestCase):
    def test_extracts_internal_links_and_adds_main_menu_label_to_submenu(self):
        html = '''
        <div class="menuTop">
          <div class="menuBox">
            <a href="?strona=wspolnota"><div>wspólnota</div></a>
            <a href="?strona=wspolnota&amp;podstrona=przymierze"><div>przymierze</div></a>
            <a href="?strona=wspolnota&amp;podstrona=diakonie"><div>diakonie</div></a>
          </div>
          <div class="menuBox"><a href="?strona=kontakt">kontakt</a></div>
        </div>
        <footer><a href="/politykaprywatnosci.html">polityka prywatności</a></footer>
        <a href="https://outside.example/">zewnętrzny</a>
        <a href="mailto:test@example.org">e-mail</a>
        <a href="#">akcja JavaScript</a>
        '''

        suggestions = extract_link_suggestions(html, "https://example.org/", frozenset({"example.org"}))

        self.assertEqual(
            [(item.label, item.url) for item in suggestions],
            [
                ("Strona główna", "https://example.org/"),
                ("wspólnota", "https://example.org/?strona=wspolnota"),
                ("wspólnota → przymierze", "https://example.org/?strona=wspolnota&podstrona=przymierze"),
                ("wspólnota → diakonie", "https://example.org/?strona=wspolnota&podstrona=diakonie"),
                ("kontakt", "https://example.org/?strona=kontakt"),
                ("polityka prywatności", "https://example.org/politykaprywatnosci.html"),
            ],
        )

    def test_uses_titles_and_repairs_legacy_query_links_without_duplicates(self):
        html = '''
        <a href="?"><img src="logo.png"></a>
        <a href="?strona=modlitwa&amp;podstrona=wstawiennicza">Modlitwa wstawiennicza</a>
        <a href="strona=modlitwa&amp;podstrona=wstawiennicza"><h1>Duplikat z karuzeli</h1></a>
        <a href="?strona=kontakt"><span title="Kontakt z nami"></span></a>
        '''

        suggestions = extract_link_suggestions(html, "https://example.org/", frozenset({"example.org"}))

        self.assertEqual(suggestions[0].label, "Strona główna")
        self.assertEqual(suggestions[-1].label, "Kontakt z nami")
        self.assertEqual(
            [item.url for item in suggestions].count(
                "https://example.org/?strona=modlitwa&podstrona=wstawiennicza"
            ),
            1,
        )
