from __future__ import annotations

from agent.models import ToolCallResult, VerificationResult


class AgentVerifier:
    def verify(self, tool_results: tuple[ToolCallResult, ...]) -> VerificationResult:
        if not tool_results:
            return VerificationResult(ok=True, status="no_tool_needed", message="No tool execution was required.")
        failed = [result for result in tool_results if not result.success]
        if failed:
            names = ", ".join(result.tool_name for result in failed)
            details = "; ".join(
                f"{result.tool_name}:{result.status}{' - ' + result.error if result.error else ''}"
                for result in failed[:3]
            )
            return VerificationResult(
                ok=False,
                status="tool_failed",
                message=f"Failed tools: {names}. Details: {details}",
            )

        weak = [result for result in tool_results if result.status not in {"completed", "no_tool_needed"}]
        if weak:
            names = ", ".join(result.tool_name for result in weak)
            return VerificationResult(
                ok=True,
                status="completed_with_warnings",
                message=f"Tool calls finished but returned non-standard statuses: {names}.",
            )

        evidence = []
        for result in tool_results:
            if "report_path" in result.result:
                evidence.append(str(result.result["report_path"]))
            if "final_mp4" in result.result:
                evidence.append(str(result.result["final_mp4"]))
            if "url" in result.result:
                evidence.append(str(result.result["url"]))
        suffix = f" Evidence: {', '.join(evidence[:3])}." if evidence else ""
        return VerificationResult(ok=True, status="verified", message=f"All tool calls completed successfully.{suffix}")
