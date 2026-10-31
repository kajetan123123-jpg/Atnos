from __future__ import annotations

from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unicodedata
import unittest

from atnos.core.config import Settings
from atnos.services.local_short_runtime_probe_service import LocalShortRuntimeComponent, LocalShortRuntimeSnapshot
from content.script_generator import ScriptGenerator, ShortScript
from content.script_quality_gate import ScriptQualityGate
from content.short_agent import ShortAgent
from content.subtitles import _script_aligned_srt
from content.youtube_package import build_youtube_release_package


class FakeGenerator(ScriptGenerator):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.calls = 0

    def generate(self, *, topic: str, brief: str) -> ShortScript:
        self.calls += 1
        if self.calls == 1:
            return ShortScript(
                title="Ogólny materiał",
                hook="To ważne.",
                body=("W dzisiejszych czasach warto pamiętać o różnych aspektach.",),
                cta="Daj lajka i subskrybuj.",
                voiceover="To ważne. W dzisiejszych czasach warto pamiętać o różnych aspektach. Daj lajka i subskrybuj.",
            )
        return _good_script(topic)


class BadGenerator(ScriptGenerator):
    def generate(self, *, topic: str, brief: str) -> ShortScript:
        return ShortScript(
            title="Ogólny materiał",
            hook="To ważne.",
            body=("W dzisiejszych czasach warto pamiętać o różnych aspektach.",),
            cta="Daj lajka i subskrybuj.",
            voiceover="To ważne. W dzisiejszych czasach warto pamiętać o różnych aspektach. Daj lajka i subskrybuj.",
        )


class FakeTts:
    def generate(self, *, text: str, output_dir: Path) -> Path:
        path = output_dir / "narration.wav"
        path.write_bytes(b"RIFFfake-wave")
        return path


class FakeSubtitles:
    def generate(self, *, audio_path: Path, output_dir: Path, fallback_text: str | None = None) -> Path:
        path = output_dir / "subtitles.srt"
        path.write_text("1\n00:00:00,000 --> 00:00:02,000\nTest\n", encoding="utf-8")
        return path


class FakeRenderer:
    def render(self, *, frame_paths: list[Path], audio_path: Path, subtitles_path: Path, output_dir: Path):
        path = output_dir / "final.mp4"
        path.write_bytes(b"fake-mp4")
        return path, {"qa": {"duration_sec": 30.0, "width": 1080, "height": 1920, "has_audio": True, "warnings": []}}


class FakeReadyRuntimeProbe:
    def snapshot(self) -> LocalShortRuntimeSnapshot:
        return LocalShortRuntimeSnapshot(
            enabled=True,
            ready=True,
            reason="test runtime ready",
            components=(
                LocalShortRuntimeComponent("ffmpeg", "FFmpeg", True, "ffmpeg", "ready"),
                LocalShortRuntimeComponent("ollama", "Ollama", True, "http://127.0.0.1:11434", "ready"),
                LocalShortRuntimeComponent("piper", "Piper", True, "piper", "ready"),
                LocalShortRuntimeComponent("whisper_cpp", "whisper.cpp", True, "whisper", "ready"),
            ),
        )


class FakeMissingRuntimeProbe:
    def snapshot(self) -> LocalShortRuntimeSnapshot:
        return LocalShortRuntimeSnapshot(
            enabled=True,
            ready=False,
            reason="Brakuje gotowych runtime'ow: Ollama, Piper.",
            components=(
                LocalShortRuntimeComponent("ffmpeg", "FFmpeg", True, "ffmpeg", "ready"),
                LocalShortRuntimeComponent("ollama", "Ollama", False, "http://127.0.0.1:11434", "not reachable"),
                LocalShortRuntimeComponent("piper", "Piper", False, None, "not configured"),
                LocalShortRuntimeComponent("whisper_cpp", "whisper.cpp", True, "whisper", "ready"),
            ),
        )


class ShortAgentTest(unittest.TestCase):
    def test_quality_gate_scores_good_script_above_threshold(self) -> None:
        report = ScriptQualityGate(min_score=7).evaluate(
            script=_good_script("lokalne AI na Windows"),
            topic="lokalne AI na Windows",
        )

        self.assertGreaterEqual(report.overall_score, 7)
        self.assertTrue(report.passed)

    def test_quality_gate_rejects_short_voiceover_even_when_sections_duplicate_text(self) -> None:
        script = ShortScript(
            title="Lokalne AI",
            hook="Czy wiesz, że AI może działać lokalnie?",
            body=(
                "Przykład 1: edytuj zdjęcia bez internetu.",
                "Przykład 2: przetwarzaj dane szybciej niż w chmurze.",
                "Przykład 3: nie płać za subskrypcję.",
            ),
            cta="Sprawdź, jak to działa!",
            voiceover=(
                "Lokalne AI na laptopie to sensowna inwestycja. "
                "Edytuj zdjęcia bez internetu. Przetwarzaj dane lokalnie. "
                "Nie płać za zbędne narzędzia. Sprawdź, jak to działa!"
            ),
        )

        report = ScriptQualityGate(min_score=7).evaluate(script=script, topic="lokalne AI na laptopie")

        self.assertFalse(report.passed)
        self.assertLess(report.overall_score, 7)
        self.assertTrue(any("voiceover" in issue for issue in report.issues))

    def test_script_generator_normalizes_voiceover_to_start_with_hook_and_minimum_length(self) -> None:
        script = ScriptGenerator._normalize_script(
            {
                "title": "Lokalne AI",
                "hook": "Czy wiesz, że AI może działać lokalnie?",
                "body": [
                    "Przykład 1: edytuj zdjęcia bez internetu.",
                    "Przykład 2: analizuj prywatne dokumenty lokalnie.",
                    "Przykład 3: testuj automatyzacje bez wysyłania plików do chmury.",
                ],
                "cta": "Zapisz tę checklistę przed pierwszym testem.",
                "voiceover": "Lokalne AI jest praktyczne, ale ten tekst nie zaczyna się od hooka.",
            },
            topic="lokalne AI na laptopie",
        )

        self.assertTrue(script.voiceover.startswith(script.hook))
        self.assertGreaterEqual(len(re.findall(r"\w+", script.voiceover, flags=re.UNICODE)), 65)

    def test_script_generator_softens_absolute_claims_before_quality_gate(self) -> None:
        script = ScriptGenerator._normalize_script(
            {
                "title": "AI bez opłat",
                "hook": "AI może zmienić Twoje życie!",
                "body": [
                    "Brak opłat i brak opóźnień to największa zaleta.",
                    "Modele lokalne są bezpieczniejsze.",
                    "Nie musisz się martwić o wycieki danych osobowych.",
                ],
                "cta": "Sprawdź, jak to działa.",
                "voiceover": (
                    "AI może zmienić Twoje życie! Brak opłat i brak opóźnień to największa zaleta. "
                    "Modele lokalne są bezpieczniejsze. Nie musisz się martwić o wycieki danych osobowych."
                ),
            },
            topic="lokalne AI na laptopie",
        )

        combined = " ".join((script.title, script.hook, *script.body, script.cta, script.voiceover)).casefold()
        self.assertNotIn("brak opłat", combined)
        self.assertNotIn("brak opóźnień", combined)
        self.assertNotIn("może zmienić twoje życie", combined)

    def test_quality_gate_penalizes_absolute_claims_and_numbered_voiceover(self) -> None:
        hook = "Nie musisz mieć internetu, by mieć AI."
        script = ShortScript(
            title="Lokalne AI",
            hook=hook,
            body=(
                "Prywatność: pracuj na własnych notatkach lokalnie.",
                "Offline: przygotuj tłumaczenie dokumentu bez wysyłania pliku.",
                "Koszt: zacznij od jednego lokalnego modelu i sprawdź efekt.",
            ),
            cta="Zapisz tę checklistę przed pierwszym testem.",
            voiceover=(
                f"{hook} 1. Prywatność: nie wysyłasz notatek do chmury. "
                "2. Offline: możesz przygotować tłumaczenie dokumentu bez połączenia. "
                "3. Koszt: brak opłat i brak opóźnień brzmi kusząco, ale wymaga sprawdzenia w praktyce. "
                "Najważniejsze: zacznij od jednego pliku, oceń wynik i dopiero wtedy rozbuduj workflow."
            ),
        )

        report = ScriptQualityGate(min_score=7).evaluate(script=script, topic="lokalne AI na laptopie")

        self.assertFalse(report.passed)
        self.assertIn("Voiceover brzmi jak lista zamiast narracji.", report.issues)
        self.assertTrue(any("dowodu" in issue for issue in report.issues))

    def test_quality_gate_rejects_unsupported_percentage_metrics(self) -> None:
        hook = "Ten blad zabija faceless shorty szybciej niz slaby montaz."
        script = ShortScript(
            title="Faceless shorty i metryki",
            hook=hook,
            body=(
                "Atnos ma najpierw sprawdzic hook, tempo, napisy i zgodnosc z planem.",
                "Nie wolno dodawac liczb, ktore brzmia madrze, ale nie maja zrodla.",
                "Lepszy jest konkretny przyklad: pokaz kadr, blad i poprawiona wersje.",
            ),
            cta="Zapisz ten test przed kolejnym renderem.",
            voiceover=(
                f"{hook} 70% generowanych shortow przegrywa przez zly pierwszy kadr. "
                "Atnos ma najpierw sprawdzic hook, tempo, napisy i zgodnosc z planem. "
                "Nie wolno dodawac liczb, ktore brzmia madrze, ale nie maja zrodla. "
                "Lepszy jest konkretny przyklad: pokaz kadr, blad i poprawiona wersje. "
                "Zapisz ten test przed kolejnym renderem."
            ),
        )

        report = ScriptQualityGate(min_score=7).evaluate(script=script, topic="faceless shorty")

        self.assertFalse(report.passed)
        self.assertTrue(any("metryki/procentu" in issue for issue in report.issues))

    def test_quality_gate_rejects_repeated_voiceover_sentence(self) -> None:
        repeated = "Najważniejsze jest pokazanie jednego realnego przykładu zamiast ogólnej obietnicy."
        script = ShortScript(
            title="Lokalne AI",
            hook="Nie musisz czekać na internet!",
            body=(
                "Pokaż jeden lokalny model na prywatnym pliku.",
                "Wyjaśnij kiedy praca offline realnie pomaga.",
                "Zakończ prostą checklistą do testu.",
            ),
            cta="Zapisz checklistę przed pierwszym testem.",
            voiceover=(
                "Nie musisz czekać na internet! Lokalny model może pomóc w pracy na prywatnych notatkach, "
                "gdy chcesz sprawdzić pomysł bez wysyłania pliku do zewnętrznej usługi. "
                f"{repeated} {repeated}"
            ),
        )

        report = ScriptQualityGate(min_score=7).evaluate(script=script, topic="lokalne AI na laptopie")

        self.assertFalse(report.passed)
        self.assertIn("Voiceover powtarza to samo zdanie.", report.issues)

    def test_quality_gate_rejects_generic_fallback_script(self) -> None:
        script = ScriptGenerator._fallback_script("lokalne AI na laptopie")

        report = ScriptQualityGate(min_score=7).evaluate(script=script, topic="lokalne AI na laptopie")

        self.assertFalse(report.passed)
        self.assertTrue(any(issue.startswith("Skrypt brzmi") for issue in report.issues))

    def test_quality_gate_rejects_meta_hook_that_quotes_topic(self) -> None:
        script = ShortScript(
            title="Meta fallback",
            hook='Najwiekszy blad przy temacie "kot robi shorty" to ogolny plan.',
            body=(
                "Najpierw pokaz konkretny problem widoczny na ekranie.",
                "Potem pokaz jak QA odrzuca slaby kadr.",
                "Na koncu pokaz poprawiony storyboard i prosty rezultat.",
            ),
            cta="Zapisz checkliste przed kolejnym renderem.",
            voiceover=(
                'Najwiekszy blad przy temacie "kot robi shorty" to ogolny plan. '
                "Najpierw pokaz konkretny problem widoczny na ekranie. "
                "Potem pokaz jak QA odrzuca slaby kadr. "
                "Na koncu pokaz poprawiony storyboard i prosty rezultat. "
                "Zapisz checkliste przed kolejnym renderem."
            ),
        )

        report = ScriptQualityGate(min_score=7).evaluate(script=script, topic="kot robi shorty")

        self.assertFalse(report.passed)
        self.assertTrue(any("cytuje temat" in issue for issue in report.issues))

    def test_automation_fallback_uses_specific_cat_short_script(self) -> None:
        script = ScriptGenerator._automation_fallback_script(
            "Ten kot odkryl blad AI shortow: bez planu i QA kanal wyglada jak slop"
        )

        report = ScriptQualityGate(min_score=7).evaluate(script=script, topic="kot AI shorty QA")

        self.assertIn("kot", script.title.casefold())
        self.assertNotIn("przy temacie", script.hook.casefold())
        self.assertTrue(report.passed)

    def test_automation_fallback_uses_finance_script_for_raise_topic(self) -> None:
        topic = "Największy błąd finansowy ludzi po pierwszej podwyżce"
        script = ScriptGenerator._automation_fallback_script(topic)

        report = ScriptQualityGate(min_score=7).evaluate(script=script, topic=topic)
        serialized = " ".join((script.title, script.hook, *script.body, script.cta, script.voiceover)).casefold()

        self.assertTrue(report.passed, report.issues)
        self.assertIn("podwy", serialized)
        self.assertIn("styl", serialized)
        self.assertNotIn("slop", serialized)
        self.assertNotIn("render", serialized)
        self.assertNotIn("kadrze", serialized)
        self.assertNotIn("sceny albo qa", serialized)

    def test_automation_fallback_uses_specific_asset_ranking_script(self) -> None:
        topic = "Aktywa, ktore naprawde robia pieniadze, ranking w 60 sekund"
        script = ScriptGenerator._automation_fallback_script(topic)

        report = ScriptQualityGate(min_score=7).evaluate(script=script, topic=topic)
        serialized = " ".join((script.title, script.hook, *script.body, script.cta, script.voiceover)).casefold()
        normalized = "".join(
            char for char in unicodedata.normalize("NFKD", serialized) if not unicodedata.combining(char)
        )

        self.assertTrue(report.passed, report.issues)
        self.assertIn("gotowk", normalized)
        self.assertIn("etf", normalized)
        self.assertIn("cashflow", normalized)
        self.assertNotIn("przekupienie", normalized)

    def test_normalize_script_falls_back_for_bad_asset_ranking_script(self) -> None:
        topic = "Aktywa, ktore naprawde robia pieniadze, ranking w 60 sekund"

        script = ScriptGenerator._normalize_script(
            {
                "title": topic,
                "hook": "Najwiekszy blad przy inwestowaniu to nie wybor, a brak kontroli.",
                "body": [
                    "Wiekszosc inwestorow kupuje, a potem zapomina.",
                    "Poprawna metoda to sledzic, analizowac i decydowac.",
                    "Zamiast tego decyduj sie na przekupienie.",
                ],
                "cta": "Zapisz checkliste i zacznij inwestowac madrze.",
                "voiceover": (
                    "Najwiekszy blad przy inwestowaniu to nie wybor, a brak kontroli. "
                    "Wiekszosc inwestorow kupuje, a potem zapomina. "
                    "Zamiast tego decyduj sie na przekupienie."
                ),
            },
            topic=topic,
        )
        serialized = " ".join((script.title, script.hook, *script.body, script.cta, script.voiceover)).casefold()

        self.assertIn("cashflow", serialized)
        self.assertNotIn("przekupienie", serialized)

    def test_quality_gate_rejects_content_pipeline_drift_for_finance_topic(self) -> None:
        script = ShortScript(
            title="Krótko o podwyżce",
            hook="Bez jednego konkretu short wyglada jak slop.",
            body=(
                "Pierwsze dwie sekundy musza pokazac problem, nie wstep.",
                "Potem potrzebny jest jeden mechanizm, ktory widz rozumie od razu.",
                "Na koncu zostaje prosta decyzja: popraw hook, sceny albo QA.",
                "Jesli tego brakuje, automatyzacja tylko szybciej produkuje chaos.",
            ),
            cta="Zapisz te checkliste przed kolejnym renderem.",
            voiceover=(
                "Bez jednego konkretu short wyglada jak slop. "
                "Pierwsze dwie sekundy musza pokazac problem, nie wstep. "
                "Potem potrzebny jest jeden mechanizm, ktory widz rozumie od razu. "
                "Na koncu zostaje prosta decyzja: popraw hook, sceny albo QA. "
                "Jesli tego brakuje, automatyzacja tylko szybciej produkuje chaos. "
                "Zapisz te checkliste przed kolejnym renderem."
            ),
        )

        report = ScriptQualityGate(min_score=7).evaluate(
            script=script,
            topic="Największy błąd finansowy ludzi po pierwszej podwyżce",
        )

        self.assertFalse(report.passed)
        self.assertTrue(any("odpływa" in issue for issue in report.issues))

    def test_quality_gate_rejects_asset_ranking_without_assets(self) -> None:
        script = ShortScript(
            title="Aktywa ranking",
            hook="Najwiekszy blad przy inwestowaniu to nie wybor, a brak kontroli.",
            body=(
                "Wiekszosc inwestorow kupuje, a potem zapomina.",
                "Poprawna metoda to sledzic, analizowac i decydowac.",
                "Zamiast tego decyduj sie na przekupienie.",
            ),
            cta="Zapisz checkliste i zacznij inwestowac madrze.",
            voiceover=(
                "Najwiekszy blad przy inwestowaniu to nie wybor, a brak kontroli. "
                "Wiekszosc inwestorow kupuje, a potem zapomina. "
                "Poprawna metoda to sledzic, analizowac i decydowac. "
                "Zamiast tego decyduj sie na przekupienie. "
                "Zapisz checkliste i zacznij inwestowac madrze."
            ),
        )

        report = ScriptQualityGate(min_score=7).evaluate(
            script=script,
            topic="Aktywa, ktore naprawde robia pieniadze, ranking w 60 sekund",
        )

        self.assertFalse(report.passed)
        self.assertTrue(any("Ranking aktywow" in issue for issue in report.issues))

    def test_quality_gate_rejects_observed_anti_slop_language_errors(self) -> None:
        script = ShortScript(
            title="3 błędy przez które faceless kanal wygląda jak tani spam",
            hook="Twoja kanal wygląda jak tani spam?",
            body=(
                "Używasz tego samego szablonu co 100 innych – nie ma autentyczności.",
                "Brak wizualnych elementów – nie wiadomo, kto stoi za treścią.",
                "Nie odpowiedzisz na komentarze – wygląda jak automat.",
            ),
            cta="Sprawdź, czy twoja kanal ma te błędy.",
            voiceover=(
                "Twoja kanal wygląda jak tani spam? Trzy błędy sprawiają, że widzowie nie wierzą w autentyczność. "
                "Używasz tego samego szablonu co 100 innych – nie ma autentyczności. "
                "Brak wizualnych elementów – nie wiadomo, kto stoi za treścią. "
                "Nie odpowiedzisz na komentarze – wygląda jak automat. Sprawdź, czy twoja kanal ma te błędy."
            ),
        )

        report = ScriptQualityGate(min_score=7).evaluate(script=script, topic="faceless kanał AI")

        self.assertFalse(report.passed)
        self.assertLess(report.polish_quality_score, 7)
        self.assertTrue(any("Błąd językowy" in issue for issue in report.issues))

    def test_quality_gate_rejects_observed_ai_tool_hallucination(self) -> None:
        script = ShortScript(
            title="Submagic, Klap i CapCut – do czego realnie użyć każdego narzędzia?",
            hook="Za 2 sekundy zrozumiesz, jak te narzędzia naprawdę działają!",
            body=(
                "Submagic? To idealne do generowania tekstu – np. stworzysz szybko napisy do wideo albo opis do zdjęcia.",
                "Klap? To narzędzie do edycji wideo – np. usunięcie znieczulenia albo dodanie efektu do klatki.",
                "CapCut? To do dodawania muzyki, przejść czy edycji długości – np. przycięcie niespotrzebnych sekund.",
            ),
            cta="Spróbuj teraz – wideo w 2 sekundy!",
            voiceover=(
                "Za 2 sekundy zrozumiesz, jak te narzędzia naprawdę działają. "
                "Submagic? To idealne do generowania tekstu – np. stworzysz szybko napisy do wideo albo opis do zdjęcia. "
                "Klap? To narzędzie do edycji wideo – np. usunięcie znieczulenia albo dodanie efektu do klatki. "
                "CapCut? To do dodawania muzyki, przejść czy edycji długości – np. przycięcie niespotrzebnych sekund. "
                "Spróbuj teraz – wideo w 2 sekundy!"
            ),
        )

        report = ScriptQualityGate(min_score=7).evaluate(script=script, topic="Submagic Klap CapCut")

        self.assertFalse(report.passed)
        self.assertLess(report.clarity_score, 7)
        self.assertTrue(any("Nonsensowny przykład" in issue or "Nieprecyzyjny opis" in issue for issue in report.issues))

    def test_script_generator_supporting_sentences_do_not_add_raw_ai_short_meta_filler(self) -> None:
        script = ScriptGenerator._normalize_script(
            {
                "title": "Submagic, Klap i CapCut",
                "hook": "Te trzy narzędzia nie robią tego samego.",
                "body": ["Klap wybiera fragmenty, Submagic wzmacnia napisy, CapCut domyka montaż."],
                "cta": "Porównaj efekt przed finalnym eksportem.",
                "voiceover": "Te trzy narzędzia nie robią tego samego.",
            },
            topic="AI short: Submagic, Klap i CapCut - do czego realnie użyć każdego narzędzia",
        )

        normalized = script.voiceover.casefold()

        self.assertNotIn("ai short:", normalized)
        self.assertNotIn("potraktuj", normalized)
        self.assertNotIn("jak mały workflow", normalized)

    def test_script_generator_does_not_append_public_source_noise_to_voiceover(self) -> None:
        topic = (
            "Odtworz mechanike formatu 'ai_animal_microstory' z filmu "
            "'😭Poor cat became a fashion designer|Fashion dream💥#catshorts#cutecat' "
            "bez kopiowania skryptu, audio, kadrow, postaci ani brandingu. "
            "Zrob oryginalny short: Oryginalny kot-agent odkrywa blad w automatycznym short pipeline i naprawia go przez QA. "
            "Hook: Ten kot zatrzymal slabego shorta. Sygnal: Ai Cat stories, 1809945 views."
        )
        script = ScriptGenerator._normalize_script(
            {
                "title": "Kot kontra QA",
                "hook": "Ten kot zatrzymal slabego shorta.",
                "body": [
                    "Najpierw widzi blad w scenie.",
                    "Potem porownuje plan z finalnym kadrem.",
                ],
                "cta": "Zapisz test QA.",
                "voiceover": (
                    "Ten kot zatrzymal slabego shorta. Najpierw widzi blad w scenie. "
                    "Potem porownuje plan z finalnym kadrem. Zapisz test QA."
                ),
            },
            topic=topic,
        )

        normalized = script.voiceover.casefold()

        self.assertNotIn("poor cat", normalized)
        self.assertNotIn("fashion dream", normalized)
        self.assertNotIn("catshorts", normalized)
        self.assertNotIn("1809945", normalized)
        self.assertIn("kot-agent", normalized)

    def test_quality_gate_rejects_public_source_leakage(self) -> None:
        script = ShortScript(
            title="Kot kontra QA",
            hook="Ten kot zatrzymal slabego shorta.",
            body=(
                "Najpierw pokaz blad w automatycznym pipeline.",
                "Potem pokaz poprawiony kadr po kontroli QA.",
                "Na koncu porownaj efekt przed i po naprawie.",
            ),
            cta="Zapisz test QA przed publikacja.",
            voiceover=(
                "Ten kot zatrzymal slabego shorta. Najpierw pokaz blad w automatycznym pipeline. "
                "Potem pokaz poprawiony kadr po kontroli QA. Fashion dream #catshorts 1809945 views. "
                "Na koncu porownaj efekt przed i po naprawie. Zapisz test QA przed publikacja."
            ),
        )

        report = ScriptQualityGate(min_score=7).evaluate(script=script, topic="faceless cat short QA")

        self.assertFalse(report.passed)
        self.assertTrue(any("researchu" in issue or "metryki" in issue or "tytułu" in issue for issue in report.issues))

    def test_script_aligned_srt_does_not_truncate_long_valid_voiceover(self) -> None:
        words = [f"word{index}" for index in range(120)]
        srt = _script_aligned_srt(" ".join(words), total_duration_sec=60)

        self.assertIn("word119", srt)

    def test_youtube_release_package_sanitizes_public_source_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            (output_dir / "visual_quality_report.json").write_text(
                '{"passed": true, "overall_score": 9.0, "issues": []}',
                encoding="utf-8",
            )
            topic = (
                "Odtworz mechanike formatu z filmu 'From Poor Cat to Fashion designer #catshorts' "
                "Zrob oryginalny short: Oryginalny kot-agent naprawia blad przez QA. "
                "Sygnal: mini meownmeow, 1057939 views."
            )
            script = ShortScript(
                title="Kot-Agent i QA",
                hook="Najwiekszy blad w pipeline to brak QA.",
                body=("Kot-agent wykrywa blad.", "QA blokuje slaby render."),
                cta="Zapisz checklistę QA.",
                voiceover="Najwiekszy blad w pipeline to brak QA. Kot-agent wykrywa blad. QA blokuje slaby render. Zapisz checklistę QA.",
            )
            quality = ScriptQualityGate(min_score=7).evaluate(script=script, topic="kot-agent QA")

            package = build_youtube_release_package(
                topic=topic,
                script=script,
                output_dir=output_dir,
                render_qa={"ok": True, "youtube_shorts_ready": True, "warnings": []},
                quality_report=quality,
            )

            serialized = (package.description + " " + " ".join(package.tags)).casefold()
            self.assertNotIn("poor cat", serialized)
            self.assertNotIn("fashion designer", serialized)
            self.assertNotIn("catshorts", serialized)
            self.assertNotIn("1057939", serialized)
            self.assertIn("kot-agent", serialized)

    def test_short_agent_repairs_and_writes_expected_artifacts(self) -> None:
        settings = self._settings()
        generator = FakeGenerator(settings)
        result = ShortAgent(
            settings,
            generator=generator,
            tts=FakeTts(),
            subtitles=FakeSubtitles(),
            renderer=FakeRenderer(),
            runtime_probe=FakeReadyRuntimeProbe(),
        ).run("lokalne AI na Windows")

        self.assertEqual(result.status, "render_qa_failed")
        self.assertEqual(result.repair_attempts, 1)
        self.assertTrue((result.output_dir / "script.md").exists())
        self.assertTrue((result.output_dir / "hook_variants.json").exists())
        self.assertTrue((result.output_dir / "short_asset_plan.json").exists())
        self.assertTrue((result.output_dir / "visual_direction.md").exists())
        self.assertTrue((result.output_dir / "quality_report.md").exists())
        self.assertTrue((result.output_dir / "narration.wav").exists())
        self.assertTrue((result.output_dir / "subtitles.srt").exists())
        self.assertTrue((result.output_dir / "thumbnail.png").exists())
        self.assertTrue((result.output_dir / "visual_storyboard.json").exists())
        self.assertTrue((result.output_dir / "visual_frames" / "scene_01.png").exists())
        self.assertTrue((result.output_dir / "final.mp4").exists())
        self.assertTrue((result.output_dir / "render_qa.json").exists())
        self.assertTrue((result.output_dir / "youtube_upload_package.json").exists())
        self.assertTrue((result.output_dir / "production_report.md").exists())
        self.assertIn("hook_variants", result.artifacts)
        self.assertIn("short_asset_plan", result.artifacts)
        self.assertIn("visual_direction", result.artifacts)
        self.assertIn("pre_render_quality_report", result.artifacts)
        self.assertIn("youtube_upload_package", result.artifacts)
        self.assertIsNotNone(result.quality_run)
        self.assertIn(result.quality_run["status"], {"DONE_VERIFIED", "DONE_NEEDS_REVIEW"})
        self.assertTrue(Path(str(result.quality_run["report_path"])).exists())
        report_text = (result.output_dir / "production_report.md").read_text(encoding="utf-8")
        self.assertIn("YouTube upload package", report_text)
        self.assertIn("Quality runtime", report_text)
        self.assertIn("render_qa_failed", (settings.data_dir / "logs" / "short_pipeline.jsonl").read_text(encoding="utf-8"))

    def test_short_agent_returns_needs_human_review_after_three_failed_repairs(self) -> None:
        settings = self._settings(min_score=11)
        result = ShortAgent(
            settings,
            generator=BadGenerator(settings),
            tts=FakeTts(),
            subtitles=FakeSubtitles(),
            renderer=FakeRenderer(),
            runtime_probe=FakeReadyRuntimeProbe(),
        ).run("bardzo ogólny temat", force_repair=True)

        self.assertEqual(result.status, "NEEDS_HUMAN_REVIEW")
        self.assertEqual(result.repair_attempts, 3)
        self.assertIsNone(result.final_mp4)
        self.assertIsNotNone(result.quality_run)
        self.assertEqual(result.quality_run["status"], "FAILED_WITH_REASON")

    def test_short_agent_uses_safe_fallback_script_after_failed_live_repairs(self) -> None:
        settings = self._settings()
        result = ShortAgent(
            settings,
            generator=BadGenerator(settings),
            tts=FakeTts(),
            subtitles=FakeSubtitles(),
            renderer=FakeRenderer(),
            runtime_probe=FakeReadyRuntimeProbe(),
        ).run("lokalne AI i prywatne pliki", force_repair=True)

        self.assertEqual(result.status, "render_qa_failed")
        self.assertEqual(result.repair_attempts, 3)
        self.assertIsNotNone(result.final_mp4)
        self.assertGreaterEqual(float(result.quality_report["overall_score"]), 7.0)
        log_text = (settings.data_dir / "logs" / "short_pipeline.jsonl").read_text(encoding="utf-8")
        self.assertIn("script_fallback_after_failed_repairs", log_text)

    def test_short_agent_stops_before_generation_when_runtime_is_not_ready(self) -> None:
        settings = self._settings()
        generator = FakeGenerator(settings)
        result = ShortAgent(
            settings,
            generator=generator,
            tts=FakeTts(),
            subtitles=FakeSubtitles(),
            renderer=FakeRenderer(),
            runtime_probe=FakeMissingRuntimeProbe(),
        ).run("lokalne AI na Windows")

        self.assertEqual(result.status, "runtime_not_ready")
        self.assertEqual(generator.calls, 0)
        self.assertIsNone(result.final_mp4)
        self.assertTrue((result.output_dir / "runtime_readiness.json").exists())
        self.assertTrue((result.output_dir / "production_report.md").exists())
        self.assertFalse((result.output_dir / "brief.md").exists())
        self.assertIsNotNone(result.quality_run)
        self.assertEqual(result.quality_run["status"], "FAILED_WITH_REASON")
        self.assertIn("runtime_not_ready", (settings.data_dir / "logs" / "short_pipeline.jsonl").read_text(encoding="utf-8"))

    def _settings(self, *, min_score: float = 7.0) -> Settings:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        env_path = root / ".env"
        env_path.write_text(
            "\n".join(
                (
                    f"SHORTS_OUTPUT_DIR={root / 'data' / 'shorts'}",
                    f"SHORTS_MIN_QUALITY_SCORE={min_score}",
                )
            ),
            encoding="utf-8",
        )
        return Settings(
            app_root=root,
            data_dir=root / "data",
            shorts_output_dir=root / "data" / "shorts",
            shorts_min_quality_score=min_score,
            _env_file=env_path,
        )


def _good_script(topic: str) -> ShortScript:
    hook = f"Jeśli {topic} brzmi skomplikowanie, to właśnie dlatego warto zacząć od jednego prostego workflow!"
    body = (
        f"Najpierw wybierz jeden problem związany z {topic}, zamiast budować cały system naraz.",
        "Potem ustaw lokalne narzędzia: model, głos, napisy i render w jednym przewidywalnym katalogu.",
        "Na końcu sprawdź wynik przez QA: czy jest audio, czy napisy są krótkie i czy finalny plik nie jest pusty.",
    )
    cta = "Zapisz tę checklistę i użyj jej przed kolejnym renderem."
    return ShortScript(
        title=f"Jak zacząć z {topic}",
        hook=hook,
        body=body,
        cta=cta,
        voiceover=" ".join((hook, *body, cta)),
    )


if __name__ == "__main__":
    unittest.main()
