---
name: atnos-final-review
description: Przejrzyj końcowy diff Atnosa pod kątem konkretnych usterek, regresji i braków dowodu przed oddaniem zmiany lub na prośbę o review. Nie zastępuje implementacji ani pełnego audytu bezpieczeństwa.
---

## PURPOSE
Wykryj istotne defekty końcowej zmiany i przedstaw je przed opisem jej zalet.

## WHEN TO USE
Po spójnym etapie implementacji albo przy przeglądzie wskazanego diffu, commita lub pull requestu.

## REQUIRED WORKFLOW
1. Ustal zakres porównania i obejrzyj diff oraz tylko potrzebny kontekst w E:/Atnos/App. Nie przypisuj istniejących niezwiązanych zmian do ocenianej pracy.
2. Sprawdź kryteria akceptacji i prześledź zmieniony kontrakt do konsumentów: frontend/src, runtime-recovered/api/src/atnos/api, services lub production. Szukaj scenariusza, w którym zmiana psuje rzeczywiste zachowanie.
3. Sprawdź duplikaty implementacji, error paths, persistence/restart, kompatybilność istniejących kontraktów i dotknięte granice bezpieczeństwa. Przy pamięci porównaj docs/atnos_project_brain.md i services/project_brain_service.py; przy produkcji order_store.py i steps/order_delivery.py.
4. Oceń dowody testów, aktualność wersji, różnicę mock–LIVE oraz zgodność docs/statusów z wynikiem. Wykonaj dodatkową kontrolę tylko dla konkretnej otwartej wątpliwości.
5. Raportuj najpierw usterki: priorytet, dokładny plik i linia, trigger, skutek oraz dowód. Oddziel potwierdzony defekt od pytania wymagającego danych. Potem podaj wykonane kontrole i ograniczenia.
6. Gdy nie ma usterek do zgłoszenia, powiedz to zwięźle i wskaż materialną lukę weryfikacji, jeżeli istnieje.

## ATNOS INVARIANTS
W sprawdzanym zakresie zachowaj zapis na E:, provenance oraz UNKNOWN zamiast wymyślonych metryk. Produkcja wymaga właściciela lease, trwałych prób i QA zgodnego z plikami; ratunkowy MP4 nie zamyka regresji. Publikacja zachowuje zgodę i local_only.

## VERIFICATION
Każda uwaga musi mieć realistyczny scenariusz i zakotwiczenie w kodzie. Zweryfikuj numery linii bieżącego diffu; wynik review nie stanowi zgody na publikację ani dowodu LIVE.

## DO NOT
Nie wymyślaj usterek dla wypełnienia listy. Nie zaczynaj od pochwał. Nie przekształcaj prostej zmiany CSS lub copy w pełny audyt bezpieczeństwa i nie stosuj poprawek, gdy użytkownik zlecił wyłącznie review.
