"""Polish explanations for the user's story review, separate from English model feedback."""

MESSAGES = {
    'word_budget': 'Liczba słów nie mieści się w zakresie ustalonym dla kanału.',
    'story_structure': 'Kolejność scen nie odpowiada strukturze opowieści wybranego kanału.',
    'fiction_disclosure': 'Opis powinien jasno informować, że opowieść jest fikcją.',
    'invisible_character': 'W obsadzie obrazu umieszczono sam głos. Usuń go z listy widocznych postaci; wypowiedź może pozostać w narracji.',
    'character_reference': 'Scena odwołuje się do nieistniejącej lub powtórzonej postaci.',
    'location_reference': 'Scena odwołuje się do miejsca, którego nie ma w opisie opowieści.',
    'repetition': 'Narracja powtarza tekst z innej sceny.',
    'tts_numbers': 'Liczby w narracji trzeba zapisać słownie po angielsku, aby lektor przeczytał je poprawnie.',
    'long_segment': 'Narracja jednej ze scen jest za długa i wymaga skrócenia.',
    'narration_punctuation': 'Narracja wymaga interpunkcji i zakończonych zdań, aby lektor robił naturalne pauzy.',
    'story_ending': 'Zakończenie powinno domknąć opowieść bez wezwania do subskrypcji lub reklamy.',
    'request_locked_setup': 'Początek nie zachowuje ustalonego pomysłu: bohater stoi przed zamkniętymi na klucz drzwiami. Zmiana stanu drzwi wymaga wyjaśnienia w historii.',
    'false_factual_framing': 'Tytuł lub opis przedstawia fikcyjne wydarzenia jako prawdziwe. Trzeba usunąć tę sugestię.',
}


def story_issue_message(issue: dict) -> str:
    return MESSAGES.get(issue['code'], 'Scenariusz wymaga poprawki. Szczegóły kontroli są zapisane w folderze zlecenia.')
