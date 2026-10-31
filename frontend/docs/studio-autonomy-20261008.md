# Studio Rolek — połączenie z autonomiczną produkcją

Zmiany z 2026-10-08 dotyczą standardowego interfejsu, bez uruchamiania dodatkowego demonstratora.

## Co działa w kodzie

- `/create` przyjmuje istniejący kontrakt zlecenia lokalnego i otwiera zapisany identyfikator w `/studio/:jobId`. Ponowienie po utracie odpowiedzi zachowuje klucz idempotencji.
- Studio wyświetla `autonomous_recovery.enabled`, wykorzystane wznowienia i limit z odpowiedzi `/api/production/orders/:jobId`. Włączenie odzyskiwania nie jest przedstawiane jako dowód działającego procesu.
- Zlecenia zatrzymane, objęte autonomicznym odzyskiwaniem, nadal odświeżają status co 5 sekund. Żywy worker i etapy produkcji pochodzą z API. UI nie uruchamia dodatkowego workera z timera.
- Awaria odczytu review-package nie ukrywa zlecenia ani dostępnych akcji. Podgląd filmu i gotowość pakietu są w takim przypadku nieweryfikowalne; UI nie pokazuje ich jako ukończonych.
- Chwilowy błąd odczytu statusu pozostawia ostatni odczyt z ostrzeżeniem o nieaktualności. Wznowienie i naprawa na tym odczycie są zablokowane do udanego odświeżenia.
- Stara odpowiedź poprzedniego zlecenia nie zastępuje produkcji otwartej później. Równoległe odświeżenia zwykłego statusu są deduplikowane.
- Lista grafik odświeża się razem ze stanem zlecenia. `atnos_automatic_visual_qc:` jest opisane jako automatyczna kwalifikacja do produkcji; nie jako akceptacja wyglądu przez użytkownika.
- Statusy `failed` i `error` nie są prezentowane jako zakończenie produkcji. Etapy ComfyUI, automatycznej oceny ilustracji i renderowania Remotion mają osobne polskie nazwy.

## Celowane testy

Wynik końcowy: **82/82 PASS** w 6 plikach; **build PASS**. Build zachowuje ostrzeżenie o istniejących dużych chunkach. Log kompilacji: `E:\Atnos\App\frontend\studio-build-20261008.log`.

Zestaw obejmuje Studio, formularz zlecenia, listę produkcji, kandydatów grafik, aktywność, komunikaty i kontrakt wywołań API. Test przejścia autonomicznego podaje z API najpierw `blocked`, następnie `running` z aktywnym workerem i sprawdza, że UI nie wywołało `resume` ani `script-repair`.

Testy komponentów nie dowodzą przejścia całej produkcji ani rzeczywistego działania modelu. Ruch filmu i odsłuch nie zostały sprawdzone w tym zadaniu frontendu.

## Odbiór przez rzeczywistą aplikację

1. Zbuduj frontend przez `npm run build` w `E:\Atnos\App\frontend`, z runtime Node i cache na E:.
2. Uruchom istniejący `E:\Atnos\App\scripts\deploy_frontend_to_desktop_web.ps1` bez `-Build`. Kopiuje aktualny `frontend\dist` do `desktop-web`; nie restartuje usług ani modeli.
3. Odśwież aplikację. Otwórz `/create` i istniejący pilot `/studio/prod-d9c21367ddda`.
4. Porównaj status, worker i liczniki Studio z aktualnym `/api/production/orders/prod-d9c21367ddda`. Włączenie automatycznego odzyskiwania musi pokazywać faktyczne użycie i limit z API.
5. Przy normalnym odzyskaniu obserwuj `blocked → running` bez kliknięcia wznowienia. Sama liczba odświeżeń nie jest dowodem postępu; wymagany jest żywy worker i nowe etapy w API.
6. Sprawdź, że nowy kandydat obrazu pojawia się bez przeładowania całego Studio. Automatycznie zakwalifikowany zasób musi nadal mieć opis „Akceptacja wyglądu przez użytkownika: niewykonana”.
7. Po rzeczywistym zakończeniu pokaż film, miniaturę, opis i blokady QA. Samo `status=ready` bez kompletnego review-package nie może dawać komunikatu gotowego pakietu.

Powyższy odbiór rzeczywistej aplikacji pozostaje do wykonania przez prowadzącego test E2E. Ten podtask nie uruchamiał, nie zatrzymywał ani nie wznawiał produkcji.

## Ograniczenia istniejącego API

Obecny kontrakt przekazuje włączenie i liczniki odzyskiwania, ale nie przekazuje osobnego `next_retry_at`, heartbeat monitora ani wyniku ostatniej decyzji automatycznej naprawy. UI celowo nie twierdzi, że monitor właśnie naprawia problem, gdy nie ma na to dowodu. Informacje te można dodać do API później; obecne zatrzymania i akcje naprawy nadal są dostępne przez istniejący endpoint recovery.
