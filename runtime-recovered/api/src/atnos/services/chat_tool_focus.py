"""Narrow clear user requests to existing tools; never grants tool permissions."""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
import hashlib
import ntpath


@dataclass(frozen=True)
class ToolFocus:
    purpose: str
    tools: tuple[str, ...]
    required: tuple[str, ...]


# A prohibition is context, not a request to operate on memory. Keep genuine
# earlier read clauses while excluding e.g. "bez modyfikacji i zapisu pamięci".
_NO_SAVE_NOUN_CLAUSE = re.compile(r"\bbez\b[^.!?\n]{0,160}\b(?:zapisu|zapisywania|zapamietywania)\b[^.!?\n]*")


def natural_tool_focus(content: str) -> ToolFocus | None:
    if content.lstrip().startswith(('„', '“', '"', '>', '```')):
        return None
    text = unicodedata.normalize("NFKD", content.casefold().replace("ł", "l")).encode("ascii", "ignore").decode()
    text = " ".join(text.split())
    text = re.sub(r"^(?:(?:atnos|hej|prosze cie|prosze)[,:]?\s+)+", "", text)
    if text.startswith(("nie ", "don't ", "```", ">", '"', "„", "“", "wyjasnij", "co oznacza")):
        return None
    if re.search(r"\b(?:dodaj|zapisz|zakolejkuj)\b", text) and re.search(r"\b(?:kolejki|kolejce|zakolejkuj)\b", text):
        return ToolFocus("task_enqueue", ("ai_queue_tasks",), ("ai_queue_tasks",))
    memory_text = re.sub(r"\bnie (?:zapisuj|zapamietuj|dopisz|dopisuj)\b[^.!?]*", "", text)
    memory_text = _NO_SAVE_NOUN_CLAUSE.sub('', memory_text)
    memory = re.search(r"\b(?:obsidian\w*|obsydian\w*|pamiec\w*|pamieci\w*)\b|\bobsidian_(?:write|read|search)\b", memory_text)
    # A named saved note or an explicit cross-conversation recall is a request
    # to retrieve evidence, even when the user does not name the vault product.
    note_read = re.match(r"(?:przeczytaj|odczytaj|zacytuj|pokaz)\b", text) and re.search(r"\b(?:zapisana|zapisanej|zapisane|zapisany|notatke|notatki)\b", memory_text)
    note_reference = re.search(r'\bnotat(?:ka|ke|ki|ce)\b', memory_text)
    if note_reference and not re.search(r'\bnotatk\w*\s+prasow\w*\b', memory_text):
        note_read = note_read or re.match(
            r'(?:(?:znajdz|odszukaj|wyszukaj)\b|'
            r'(?:czy )?mozesz (?:mi )?(?:przeczytac|odczytac|pokazac|zacytowac)\b|'
            r'co (?:jest w|mamy w|zapisano w|zawiera)\b|jaka jest tresc\b)', text)
    note_write = re.match(r"(?:zapisz|utworz|zaktualizuj|popraw)\b", text) and re.search(r"\bnotatk(?:e|i)\b", memory_text)
    recall = re.search(r"\b(?:co ustalilismy|co pamietasz|przypomnij|czego sie nauczyles)\b", memory_text)
    memory = memory or note_read or note_write or recall
    if memory:
        explicit = re.match(r"(?:uzyj\s+|wywolaj\s+)?obsidian_(write|read|search)\b", text)
        # A verb inside a quoted note title is data, not permission to write.
        unquoted = re.sub(r'„[^”]*”|“[^”]*”|"[^"]*"|`[^`]*`', '', content)
        write_text = unicodedata.normalize('NFKD', unquoted.casefold().replace('ł', 'l')).encode('ascii', 'ignore').decode()
        write = re.search(r"(?<!nie )\b(?:zapisz|zapamietaj|zaktualizuj|popraw|utworz)\b", write_text)
        if note_read and write and not memory_opt_out(content):
            return None  # Retain the broader workflow for a real read+write compound.
        read = recall or note_read or re.search(r"\b(?:sprawdz|znajdz|wyszukaj|odszukaj|przeczytaj|odczytaj|zacytuj|podaj|jaki|czego)\b", text)
        if explicit or write or read:
            is_write = (explicit.group(1) == "write") if explicit else bool(write and not memory_opt_out(content))
            required = ("obsidian_write",) if is_write else ("obsidian_read",) if explicit and explicit.group(1) == "read" else ("obsidian_search",)
            if not is_write and not explicit:
                required = ("obsidian_search", "obsidian_read")
            if is_write and re.search(r"\b(?:zaktualizuj|popraw|korekta)\b", text):
                required = ("obsidian_search", "obsidian_write")
            tools = ("obsidian_search", "obsidian_read", "obsidian_write") if is_write else ("obsidian_search", "obsidian_read")
            # A URL is a second requested operation, not permission to forget the local task.
            if re.search(r"https?://", text) and re.search(r"\b(?:otworz|przeczytaj|sprawdz)\b", text):
                tools += ("browser_learn",)
                required += ("browser_learn",)
            return ToolFocus("memory_write" if is_write else "memory_read", tools, required)
    workers = re.search(r"\b(?:worker\w*|agentow|agenci|zadan\w*|petl\w*)\b", text)
    read_request = re.match(r"(?:(?:czy )?mozesz )?(?:sprawdz\w*|pokaz\w*|wyswietl\w*|jakie|jaki jest (?:stan|status)|co (?:teraz|aktualnie|robia))\b", text)
    if workers and not read_request and re.match(r"(?:(?:czy )?(?:mozesz|potrafisz|jestes)|jestes w stanie|jakie .*mozliwosci)", text):
        return ToolFocus("background_capabilities", ("ai_delegate_status",), ())
    if workers and read_request and re.search(r"\b(?:tle|status\w*|stan|robia|dzial\w*|aktywn\w*|uruchomion\w*|kolejk\w*|testu|projektu|nazwie)\b", text):
        return ToolFocus("background_status", ("ai_delegate_status",), ("ai_delegate_status",))
    capability_question = re.match(r'^(?:co (?:potrafisz|mozesz zrobic)|jakie masz (?:mozliwosci|narzedzia)|'
        r'(?:pokaz|sprawdz) swoje (?:mozliwosci|narzedzia)|czy masz dostep do (?:internetu|plikow|pamieci))\b', text)
    if capability_question and not re.search(r'\b(?:nastepnie|potem|uruchom|zapisz|utworz|usun|wygeneruj|zrob|napisz|przeczytaj|odczytaj|otworz|popraw)\b', text):
        return ToolFocus('capability_status', ('self_state',), ('self_state',))
    if re.match(r"(?:przygotuj|popraw|napraw|przeczytaj|sprawdz)\b", text) and re.search(r"propozycj\w* (?:poprawki|zmiany)|poprawk\w* kodu", text) and re.search(r"\.(?:py|tsx?|jsx?)\b", text):
        return ToolFocus("coding_proposal", ("fs_read", "ai_delegate_background"), ("fs_read", "ai_delegate_background"))
    if re.search(r"\b(?:wygeneruj|stworz|utworz|narysuj|uruchom|usun)\b", text):
        return None  # Compound requests must retain the broader existing workflow.
    if re.match(r"(?:przeczytaj|odczytaj|zacytuj|pokaz (?:zawartosc|tresc)|sprawdz(?: w repo(?:zytorium)?)? plik)\b", text) and re.search(r"\.(?:md|txt|py|tsx?|jsx?|json|csv|log)\b", text):
        return ToolFocus("file_read", ("fs_read",), ("fs_read",))
    if re.match(r"(?:pokaz|sprawdz|wypisz|wyswietl)\b", text) and re.search(r"\b(?:zawartosc folderu|zawartosc katalogu|pliki w|co jest w folderze)\b", text):
        return ToolFocus("file_list", ("fs_list",), ("fs_list",))
    return None


def format_background_status(data: dict) -> str:
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return "Rejestr nie zwrócił listy zadań; nie mogę potwierdzić ich stanu."
    active = [item for item in tasks if item.get("status") == "running"]
    lines = [f"Sprawdziłem {len(tasks)} ostatnich zleceń w rejestrze Atnosa. Wśród nich status „w trakcie” ma {len(active)}."]
    labels = {"running": "w trakcie", "completed": "wynik otrzymany", "incomplete": "nieukończone", "failed": "błąd", "blocked": "zablokowane", "planned": "zaplanowane", "pending": "oczekuje", "pending_external": "oczekuje na dostawcę", "cancelled": "anulowane"}
    for item in tasks[:50]:
        status = labels.get(item.get("status"), str(item.get("status") or "nieznany"))
        review = " — wymaga przeglądu" if item.get("verification_status") == "awaiting_human_review" else ""
        position = f"pozycja {item['sequence_index'] + 1}, " if isinstance(item.get('sequence_index'), int) and item.get('task_group') else ''
        lines.append(f"• #{item.get('id')}: {position}{item.get('summary') or item.get('task_type') or 'zadanie'}, {status}{review}.")
    lines.append("To stan zapisanych zleceń, nie kontrola wszystkich procesów PC. Niczego nie uruchomiłem.")
    return "\n".join(lines)


def direct_read_request(content: str, focus: ToolFocus | None, *, conversation_id: int | None = None) -> tuple[str, dict] | None:
    if focus is None:
        return None
    if focus.purpose == 'capability_status':
        return 'self_state', {}
    if focus.purpose == 'memory_read' and set(focus.required) == {'obsidian_search', 'obsidian_read'}:
        named = re.search(r'\bnotat(?:k[ęeai]|ce)\s+(?:[„“"]([^”"]{3,120})[”"]|([^.!?\n]{3,120}))',
                          content, re.IGNORECASE)
        if named:
            query = (named[1] or named[2]).strip()
            query = re.split(r'\s+(?:i|oraz)\s+(?:podaj|pokaż|pokaz|odczytaj|zacytuj)\b', query,
                             maxsplit=1, flags=re.IGNORECASE)[0].strip()
            if not re.match(r'^(?:o|z|dla|która|ktora|jaką|jaka)\s', query, re.IGNORECASE):
                return 'obsidian_search', {'query': query}
    if focus.purpose == "memory_write" and len(focus.required) == 1:
        # Exact title/body only: no model guesses, no inferred edits of an existing note.
        match = re.search(r'notatk[ęea](?:\s+(?:testową|testowa|learning|memory))?\s+[„"]([^”"]{3,120})[”"]\s*:\s*(.+)', content, re.DOTALL | re.IGNORECASE)
        if match and not re.search(r"\b(?:zaktualizuj|popraw|korekta)\b", content, re.IGNORECASE):
            return "obsidian_write", {"title": match[1], "body": match[2].strip(),
                "note_type": "learning" if re.search(r"notatk[ęea]\s+learning", content, re.IGNORECASE) else "memory",
                "tags": ["audit-test", "fictional"] if re.search(r"\b(?:test|audyt)", content, re.IGNORECASE) else ["user-request"]}
    if focus.purpose == "background_status":
        args = {}
        identifier = re.search(r'\b[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+){2,}\b', content)
        if identifier:
            args['query'] = identifier.group(0)
        else:
            named = re.search(r'(?:z testu|dla projektu|o nazwie)\s+[„"]([^”"]{3,120})[”"]', content, re.IGNORECASE)
            if not named:
                named = re.search(r'(?:z testu|dla projektu|o nazwie)\s+([^.!?\n]{3,120})', content, re.IGNORECASE)
            if named:
                args['query'] = re.split(r'\s+w rozmow', named.group(1), flags=re.IGNORECASE)[0].strip()
        conversation = re.search(r'rozmow[ayęie]*\s*#?(\d+)', content, re.IGNORECASE)
        if conversation:
            args['conversation_id'] = int(conversation.group(1))
        elif conversation_id is not None and re.search(r'\b(?:tej|bieżącej|biezacej|aktualnej) rozmow', content, re.IGNORECASE):
            args['conversation_id'] = conversation_id
        return "ai_delegate_status", args
    text = unicodedata.normalize("NFKD", content.casefold().replace("ł", "l")).encode("ascii", "ignore").decode()
    if focus.purpose != "file_read" or not re.search(r"\b(?:wylacznie|tylko)\s+(?:(?:jego|jej|dokladnie)\s+)?pierwsz\w*\s+(?:wiersz\w*|lini\w*)\b", text):
        return None
    paths = re.findall(r'''[A-Za-z]:[\\/][^\r\n"`]*?\.(?:md|txt|py|tsx?|jsx?|json|csv|log)\b''', content, re.IGNORECASE)
    return ("fs_read", {"path": paths[0]}) if len(paths) == 1 else None


def format_direct_read(name: str, data: dict) -> str:
    if name == 'obsidian_search':
        return ('Wyszukiwanie nie wskazało jednoznacznie notatki. Podaj dokładny tytuł lub ścieżkę.'
                if data.get('hits') else 'Nie znalazłem wskazanej notatki w wynikach wyszukiwania. Podaj dokładny tytuł lub ścieżkę.')
    if name == "obsidian_write":
        if data.get("status") != "written":
            return "Zapis notatki nie został potwierdzony."
        return f"Zapisałem notatkę „{data.get('title')}”.\nŚcieżka: {data.get('path')}\nSHA-256: {data.get('sha256')}"
    if name == "ai_delegate_status":
        return format_background_status(data)
    content = data.get("content")
    if not isinstance(content, str):
        return "Nie otrzymałem tekstu pliku; cytat nie został odczytany."
    if data.get("truncated") and "\n" not in content:
        return "Odczyt uciął pierwszy wiersz; nie mogę podać pełnego cytatu."
    return content.splitlines()[0] if content.splitlines() else "Plik jest pusty."


def ground_file_first_line(content: str, focus: ToolFocus | None, reads: list[dict], answer: str) -> tuple[str | None, dict] | None:
    """Check a requested literal excerpt against the actual successful read.

    This validates the quote only, never the model's accompanying analysis.
    A mismatch renders source evidence and remains incomplete, without retries.
    """
    text = unicodedata.normalize('NFKD', content.casefold().replace('ł', 'l')).encode('ascii', 'ignore').decode()
    if not focus or focus.purpose != 'file_read' or not re.search(r'\bpierwsz\w*\s+(?:wiersz\w*|lini\w*)\b|\bfirst line\b', text):
        return None
    paths = re.findall(r'''[A-Za-z]:[\\/][^\r\n"`]*?\.(?:md|txt|py|tsx?|jsx?|json|csv|log)\b''', content, re.IGNORECASE)
    if len(paths) != 1:
        return None  # Multiple sources need a separate explicit contract.
    normalize = lambda path: ntpath.normcase(ntpath.normpath(str(path)))
    matching = [data for data in reads if normalize(data.get('path', '')) == normalize(paths[0]) and isinstance(data.get('content'), str)]
    metadata = {'scope':'file_first_line', 'path':paths[0], 'status':'unverified'}
    if len(matching) != 1:
        return 'Nie potwierdzam cytatu: brak jednoznacznego odczytu wskazanego pliku.', metadata
    data = matching[0]
    source = data['content']
    if not source or (data.get('truncated') and '\n' not in source and '\r' not in source):
        return 'Nie potwierdzam pełnego pierwszego wiersza: plik jest pusty albo odczyt został ucięty.', metadata
    line = source.splitlines()[0]
    metadata.update(returned_content_sha256=hashlib.sha256(source.encode('utf-8')).hexdigest(),
                    first_line_sha256=hashlib.sha256(line.encode('utf-8')).hexdigest())
    if line and re.search(r'(?:^|[\n`])' + re.escape(line) + r'(?=$|[\r\n`])', answer):
        metadata['status'] = 'verified'
        return None, metadata
    fence = '`' * max(3, max((len(item) + 1 for item in re.findall(r'`+', line)), default=3))
    metadata['status'] = 'model_quote_mismatch'
    return (f'Dosłowny pierwszy wiersz ze źródła `{data["path"]}`:\n\n{fence}\n{line}\n{fence}\n\n'
            'Wyświetlam potwierdzony cytat. Pozostała odpowiedź modelu nie przeszła kontroli zgodności cytatu ze źródłem; zadanie pozostaje częściowo wykonane.'), metadata


def obsidian_read_complete(data: dict) -> bool:
    return (isinstance(data.get('markdown'), str) and data.get('truncated') is not True
            and data.get('status') not in {'incomplete', 'error', 'failed', 'blocked'})


def format_obsidian_read(data: dict) -> str:
    header = f"Źródło w Obsidianie: `{data.get('relative_path') or 'UNKNOWN'}`"
    if not obsidian_read_complete(data):
        header += (f"\nOdczyt częściowy: zwrócono {data.get('returned_chars', 'UNKNOWN')} "
                   f"z {data.get('total_chars', 'UNKNOWN')} znaków. To nie jest pełna treść notatki.")
    markdown = data.get('markdown')
    return header + '\n\n' + (markdown if isinstance(markdown, str) else 'UNKNOWN — brak treści z narzędzia.')


def memory_opt_out(content: str) -> bool:
    text = unicodedata.normalize("NFKD", content.casefold().replace("ł", "l")).encode("ascii", "ignore").decode()
    # Strip titles before ASCII normalization removes Polish quote marks.
    unquoted = re.sub(r'„[^”]*”|“[^”]*”|"[^"]*"|`[^`]*`', '', content)
    noun_context = unicodedata.normalize("NFKD", unquoted.casefold().replace("ł", "l")).encode("ascii", "ignore").decode()
    # Polish inflections (Obsidiana/Obsidianie) matter. A read-only instruction
    # forbids persistence, not merely the phrase used to acknowledge persistence.
    return bool(_NO_SAVE_NOUN_CLAUSE.search(noun_context) or re.search(
        r"\b(?:nie (?:zapisuj|zapamietuj|dopisuj)|bez zapisywania)\b[^.!?\n]{0,120}\b(?:pamiec\w*|obs[yi]dian\w*|notat\w*|bibliotek\w*)\b"
        r"|\b(?:nic(?:zego)? nie (?:zapisuj|dopisuj)|tylko odczyt|wylacznie odczyt)\b"
        r"|\bnie (?:zapisuj|zapamietuj|dopisuj)\b"
        r"|\b(?:do not|don't|dont|never)\s+(?:save|store|write|persist|remember)\b"
        r"|\bread[ -]only\b", text))


def explicit_followup_read(content: str, focus: ToolFocus | None) -> dict | None:
    """Only a separate literal read clause; a URL in the saved body is not enough."""
    if not focus or focus.purpose != 'memory_write' or 'browser_learn' not in focus.required:
        return None
    urls = re.findall(r'https?://[^\s<>„”"`]+', content)
    if len(urls) != 1:
        return None
    clause = re.search(
        r'(?:^|[.!?]\s+|\n)\s*(?:następnie|nastepnie|potem)\s+'
        r'(?:przeczytaj|odczytaj|sprawdź|sprawdz)\s+(?:stronę\s+|strone\s+)?'
        r'(https?://[^\s<>„”"`]+)', content, re.IGNORECASE)
    if not clause:
        return None
    for quoted in re.finditer(r'```[\s\S]*?```|`[^`]*`|„[^”]*”|“[^”]*”|"[^"]*"', content):
        if quoted.start() <= clause.start() < quoted.end():
            return None
    return {'url': clause.group(1).rstrip('.,;'), 'research_focus': content[clause.start():].strip()}


def named_note_source(content: str, search_result: dict) -> str | None:
    """Only choose an unambiguous note title actually named in the request.

    A mention in a note body or a high fuzzy score is not source authority.
    Multiple paths with the same title remain ambiguous and require review.
    """
    paths = named_note_sources(content, search_result)
    return paths[0] if len(paths) == 1 else None


def requests_note_comparison(content: str) -> bool:
    """A title containing 'conflict' is data, not an instruction to read more."""
    unquoted = re.sub(r'„[^”]*”|“[^”]*”|"[^"]*"|`[^`]*`', '', content)
    text = unicodedata.normalize('NFKD', unquoted.casefold().replace('ł', 'l')).encode('ascii', 'ignore').decode()
    if re.search(r'\bnie\s+(?:porownuj|pokazuj|odczytuj)\b', text):
        return False
    return bool(re.search(r'\b(?:porownaj|porownanie|sprzecznosci|roznice|konflikt)\b', text))


def named_note_sources(content: str, search_result: dict) -> list[str]:
    """Return all equally named sources, so ambiguity cannot become a PASS."""
    normalize = lambda value: ' '.join(str(value).casefold().split())
    text = normalize(content)
    matches = [hit for hit in search_result.get('hits', []) if isinstance(hit, dict)
               and len(normalize(hit.get('title') or '')) >= 3
               and normalize(hit['title']) in text and hit.get('relative_path')]
    if not matches:
        return []
    longest = max(len(normalize(hit['title'])) for hit in matches)
    paths = {str(hit['relative_path']) for hit in matches if len(normalize(hit['title'])) == longest}
    return sorted(paths)
