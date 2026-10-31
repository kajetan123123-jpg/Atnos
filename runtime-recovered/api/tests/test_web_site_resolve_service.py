from __future__ import annotations

import unittest

from atnos.services.web_site_resolve_service import WebSiteResolveService


class WebSiteResolveServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = WebSiteResolveService()

    def test_literal_url(self) -> None:
        result = self.service.resolve("https://fortecait.pl/kontakt.html")
        self.assertEqual(result.method, "literal_url")
        self.assertEqual(result.url, "https://fortecait.pl/kontakt.html")

    def test_forteca_alias(self) -> None:
        result = self.service.resolve("Forteca IT")
        self.assertEqual(result.method, "alias")
        self.assertEqual(result.url, "https://fortecait.pl/")

    def test_domain_guess(self) -> None:
        result = self.service.resolve("fortecait.pl")
        self.assertIn(result.method, {"alias", "domain_guess"})
        self.assertTrue(str(result.url or "").startswith("https://fortecait.pl"))


if __name__ == "__main__":
    unittest.main()
