from __future__ import annotations

from content.script_generator import ScriptGenerator, ShortScript
from content.script_quality_gate import ScriptQualityReport


class ScriptRepair:
    def __init__(self, generator: ScriptGenerator) -> None:
        self.generator = generator

    def repair(self, *, topic: str, brief: str, script: ShortScript, report: ScriptQualityReport) -> ShortScript:
        repair_brief = (
            f"{brief}\n\n"
            "Popraw poprzedni skrypt. Problemy:\n"
            + "\n".join(f"- {issue}" for issue in report.issues)
            + "\n\nPoprzedni voiceover:\n"
            + script.voiceover
            + "\n\nWygeneruj ostrzejszy, bardziej konkretny skrypt."
            + "\nTwarde wymagania naprawy:"
            + "\n- voiceover zaczyna sie dokladnie od hooka"
            + "\n- voiceover ma 65-130 slow"
            + "\n- voiceover brzmi jak narracja, nie jak numerowana lista"
            + "\n- unikaj absolutnych claimow bez dowodu, np. brak oplat, bez oplat, brak opoznien, bez ryzyka"
            + "\n- popraw bledy jezykowe i rodzaje gramatyczne; tekst ma brzmiec jak naturalny polski"
            + "\n- usun AI filler, np. 'w dzisiejszych czasach', 'potraktuj temat jak workflow'"
            + "\n- jesli opisujesz narzedzie, nie wymyslaj funkcji; pokaz realny, ostrozny use case"
            + "\n- CTA ma wynikac z tematu, bez clickbaitu i bez obietnic typu 'wideo w 2 sekundy'"
        )
        return self.generator.generate(topic=topic, brief=repair_brief)
