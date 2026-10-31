from __future__ import annotations

from atnos.browser_operator.models import BrowserAction, BrowserRecipe


class BrowserPlanner:
    def plan_open_page(self, *, url: str, reason: str = "Open page and inspect current state.") -> list[BrowserAction]:
        return [
            BrowserAction(
                action_type="goto",
                target=url,
                value=None,
                reason=reason,
                expected_result=f"Browser lands on {url}.",
                risk_level="low",
            )
        ]

    def plan_extract_text(self, *, url: str) -> BrowserRecipe:
        return BrowserRecipe(
            site=url,
            task="extract_text",
            steps=[
                BrowserAction("goto", url, None, "Open the page before reading.", "Page is loaded.", "low"),
                BrowserAction("observe", None, None, "Read visible text from the page.", "Visible text extracted.", "low"),
            ],
            risks=["UI may change", "Page may require login"],
            notes="Prefer DOM and visible text over screenshots.",
        )

    def plan_fill_form(
        self,
        *,
        site: str,
        task: str,
        fields: list[tuple[str, str]],
        submit: bool = False,
    ) -> BrowserRecipe:
        steps = [
            BrowserAction(
                action_type="fill_by_label",
                target=label,
                value=value,
                reason=f"Fill the field labelled {label}.",
                expected_result=f"Field {label} contains the provided value.",
                risk_level="medium",
            )
            for label, value in fields
        ]
        if submit:
            steps.append(
                BrowserAction(
                    action_type="submit",
                    target=None,
                    value=None,
                    reason="Submit the prepared form.",
                    expected_result="Form is submitted and success state is visible.",
                    risk_level="high",
                )
            )
        return BrowserRecipe(
            site=site,
            task=task,
            steps=steps,
            human_required=["submit_review"] if submit else [],
            risks=["Form validation may fail", "Modal or cookie wall may block input"],
            notes="Plan -> observe -> act -> verify. Do not submit without approval in risky flows.",
        )

    def plan_chatgpt_paste_prompt(self, *, prompt: str, auto_send: bool = False) -> BrowserRecipe:
        steps = [
            BrowserAction("goto", "https://chatgpt.com/", None, "Open ChatGPT.", "ChatGPT home is visible.", "low"),
            BrowserAction("observe", None, None, "Check whether the user is logged in.", "Composer or login wall is identified.", "low"),
            BrowserAction("paste_text", "chat_composer", prompt, "Paste the prompt into the composer.", "Prompt text is visible in the composer.", "medium"),
        ]
        if auto_send:
            steps.append(
                BrowserAction(
                    "press",
                    "Enter",
                    None,
                    "Submit the prompt after paste.",
                    "Assistant response starts streaming.",
                    "high",
                )
            )
        return BrowserRecipe(
            site="chatgpt.com",
            task="paste_prompt",
            steps=steps,
            human_required=["first_login", "captcha", "sensitive_prompt"] + ([] if auto_send else ["review_before_send"]),
            risks=["UI may change", "Account limits", "Login may be required"],
            notes="Default mode pastes the prompt and waits for human send review.",
        )
