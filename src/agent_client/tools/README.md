# `agent_client/tools/`: what external capability can be executed

**Empty in a chain pipeline, on purpose.** Every model call here is one structured answer to a
message that already holds everything the model needs. No call is handed a tool, so no module lives
in this folder, and a tool nothing calls is prompt budget spent on nothing.

The folder stays because the layout names the layer. Add a tool module here only when a phase
genuinely has to explore something it cannot be sent, and then that phase is an agent phase: give
it the engine's agent loop, not the chain loop.
