from __future__ import annotations

from content.script_generator import ShortScript
from content.script_quality_gate import ScriptQualityGate


def test_short_quality_gate_scores_concrete_polish_script() -> None:
    script = ShortScript(
        title="Lokalne AI bez chaosu",
        hook="Dlaczego lokalne AI jest przewaga, a nie tylko zabawka?",
        body=(
            "Po pierwsze, lokalne AI daje prywatnosc, bo dane zostaja na twoim komputerze.",
            "Po drugie, skraca czas pracy, bo proste zadania nie czekaja na cloud ani limity.",
            "Po trzecie, przez lokalny model mozesz testowac automatyzacje z kontrola kosztow.",
        ),
        cta="Zacznij od jednego lokalnego workflow i sprawdz wynik.",
        voiceover=(
            "Dlaczego lokalne AI jest przewaga, a nie tylko zabawka? "
            "Po pierwsze, lokalne AI daje prywatnosc, bo dane zostaja na twoim komputerze. "
            "Po drugie, skraca czas pracy, bo proste zadania nie czekaja na cloud ani limity. "
            "Po trzecie, przez lokalny model mozesz testowac automatyzacje z kontrola kosztow. "
            "To nie jest magiczny system. To jest praktyczny sposob, jak szybciej sprawdzac pomysly, "
            "pisac notatki, robic szkice i budowac workflow bez wysylania wszystkiego na zewnatrz. "
            "Zacznij od jednego lokalnego workflow i sprawdz wynik."
        ),
    )

    report = ScriptQualityGate(min_score=7.0).evaluate(script=script, topic="lokalne AI")

    assert report.passed is True
    assert report.overall_score >= 7.0
    assert set(report.to_dict()) >= {
        "hook_score",
        "clarity_score",
        "polish_quality_score",
        "retention_score",
        "overall_score",
        "passed",
        "issues",
    }
