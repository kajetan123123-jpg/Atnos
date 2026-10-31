# Local LLM and tool use

- Select only a configured provider/model for the requested task; never switch defaults or download models as a side effect of an ordinary chat turn.
- For PC actions, use the Atnos tool-call loop and wait for the tool result before describing an action as completed.
- Treat model text as a proposal, not evidence. Validate structured output against the registered contract.
- Preserve local conversation state, memory boundaries and authorization checks in Atnos; the inference server is not the memory owner.
- Log the selected provider/model, actual tool calls and their outcomes without logging secrets.
