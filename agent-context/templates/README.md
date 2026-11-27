# templates/ - what SHAPE the artifact has

Scaffolded onto disk for the worker to fill, and injected into the prompt so the shape is in front of
it while it writes.

A template holds structure, not instructions. A `## Write plan` or `## Cross-cutting rules` section
inside a template is instruction text delivered only to a worker that opens the file - it belongs in
`../instructions/`.
