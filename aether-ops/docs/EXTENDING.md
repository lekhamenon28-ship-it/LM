# Extending AETHER.OPS

Put one trusted JavaScript module in `app/features/` and register it with `Aether.registerFeature({id,label,description,icon,mount})`. The build bundles feature modules alphabetically. Each mount receives `root`, `notify`, `navigate`, `services` and `escapeHtml` helpers. Use `Workspace.mount(root,render)` to subscribe to authenticated workspace snapshots, and `Workspace.mutate(path,method,body)` to write records through the server.

All operational persistence belongs in authenticated backend routes. The platform's legacy feature-store API is session memory only and is not suitable for records. Do not add static operational metrics or seeded records to application views. Escape user-supplied text before HTML interpolation.

`worker/workspace.mjs` manages durable records, persona authorization, revision checks, tool invocation, credentials and conversations. Add routes there or in separate modules using the session validation pattern in `worker/index.mjs`. UI visibility alone is not authorization. Registered tools are fixed administrator-approved HTTPS endpoints; agent input never supplies an alternate endpoint or credential.

See README.md for permissions, execution semantics and test commands. Reuse the existing hosting project ID in `.openai/hosting.json` for updates.
