# Atnos skill: remote Linux host operations

Use only the configured remote-host capability and verify the selected host identity before acting. Report the host and the operation. Prefer read-only inspection first; make the smallest change that serves the request and verify it afterward.

Treat package installation, service changes, account changes, firewall changes, and destructive commands as high impact. Follow the tool's approval gate; do not infer blanket authorization from a prior unrelated action. Never print or repeat passwords, private keys, or tokens. Do not assume Ubuntu-specific paths or service names when the host may be Debian or another distribution; inspect the OS first. If the remote capability is absent, report that it is unavailable rather than improvising through a different channel.
