"""Capabilities for the operator UI, derived from the actual order contract."""
from typing import get_args
from sqlite3 import DatabaseError

from atnos.intelligence.storage import connect
from atnos.production.project_profile import ProductionOrder, load_project_profile

CHANNELS = (
    ("wealth_blueprint", "Wealth Blueprint", "Inwestycje", "Praktyczna edukacja finansowa i sprawdzane obliczenia."),
    ("children_stories", "Bajki dla dzieci", "Bajki", "Spokojne angielskie bajki dla dzieci 4–7 lat, prosta fabuła i pogodne zakończenie."),
    ("horror_stories", "Straszne historie", "Historie", "Angielskie fikcyjne opowieści z napięciem i mocnym zakończeniem."),
)


def build_workspace_catalog(settings):
    supported = get_args(ProductionOrder.model_fields["project"].annotation)
    channels = []
    for key, name, category, description in CHANNELS:
        item = {"key": key, "name": name, "category": category, "description": description,
                "language": "en", "can_order": False, "can_prepare_story": False, "blockers": [], "formats": []}
        if key not in supported:
            item["blockers"].append("Automatyczna produkcja tego kanału wymaga jeszcze podłączenia profilu i procesu tworzenia.")
        else:
            try:
                profile = load_project_profile(settings, key)
                config = profile["production"]
                story = key != 'wealth_blueprint'
                item.update(can_order=not story, can_prepare_story=story, formats=["script_preview"] if story else ["short"],
                            duration_seconds=config["duration_seconds"],
                            max_cost_usd=config["max_cost_usd"])
                if story:
                    item['blockers'].append('Dostępny jest scenariusz z kontrolą fabuły. Obraz, lektor i montaż opowieści są jeszcze w przygotowaniu.')
                else:
                    item['voice_provider'] = config['voice']['provider']
                    from atnos.production.local_voice_lane import intake_voice_sample, verify_runtime_availability
                    try:
                        intake_voice_sample()
                        # This endpoint is polled every ten seconds. Full weight
                        # hashing belongs to production preflight, never UI polling.
                        available = bool(verify_runtime_availability(lightweight=True).get('available'))
                        item['local_voice'] = {'id': 'Ryan-B', 'label': 'Ryan B — lokalny kandydat', 'available': available,
                            'reason': '' if available else 'Brak lokalnego runtime głosu lub wyrównania napisów.'}
                    except (OSError, RuntimeError, ValueError) as exc:
                        item['local_voice'] = {'id': 'Ryan-B', 'label': 'Ryan B — lokalny kandydat', 'available': False, 'reason': str(exc)}
                    neutral = {'id': 'Ryan-neutral', 'label': 'Ryan — spokojna interpretacja, lokalny kandydat',
                               'available': False, 'reason': ''}
                    try:
                        intake_voice_sample('Ryan-neutral')
                        neutral['available'] = bool(verify_runtime_availability(lightweight=True).get('available'))
                        if not neutral['available']:neutral['reason'] = 'Brak lokalnego runtime głosu lub wyrównania napisów.'
                    except (OSError, RuntimeError, ValueError) as exc:
                        neutral['reason'] = str(exc)
                    item['local_voices'] = [item['local_voice'], neutral]
                    from atnos.production.wb_production_set import SET_ID,freeze_production_set
                    try:
                        recipe=freeze_production_set('Ryan-neutral')
                        item['production_sets']=[{'id':SET_ID,'label':'WB Editorial v1 — obecny kierunek, lokalny',
                            'available':True,'digest':recipe['digest'],'reason':'',
                            'scope':'Obecny zestaw: jakościowe diagramy wspólnych oszczędności, rezerwy i celu. Inne mechanizmy, porównania liczb i historia Alexa wymagają osobnych kompozycji.'}]
                    except (OSError,RuntimeError,ValueError) as exc:
                        item['production_sets']=[{'id':SET_ID,'label':'WB Editorial v1','available':False,'reason':str(exc)}]
                with connect(settings) as db:
                    account_exists = db.execute("SELECT 1 FROM intelligence_accounts WHERE account_id=?",
                                                (profile["account_id"],)).fetchone()
                if not account_exists:
                    item["can_order"] = False
                    item['can_prepare_story'] = False
                    item["blockers"].append("Profil kanału nie jest połączony z kontem produkcji. Uzupełnij konfigurację kanału w ustawieniach.")
            except (OSError, ValueError, KeyError, DatabaseError):
                item["can_order"] = False
                item['can_prepare_story'] = False
                item["blockers"].append("Profil produkcji jest niedostępny lub niepoprawny. Sprawdź konfigurację kanału.")
        channels.append(item)
    return {"channels": channels, "delivery_mode": "local_only",
            "note": "Dostępność zlecenia nie potwierdza aktywnego połączenia z dostawcami. Każda produkcja sprawdza zależności przed startem."}
