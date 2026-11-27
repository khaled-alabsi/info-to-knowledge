<!-- sync: seed/chains.README v1 -->
Chains: `prompt | model [| schema]`, with `schemas/` holding the structured contract.

**This layer is optional, and it arrives with `schemas/` or not at all.** A playbook whose every
step is an agent doing exploratory work asks a model for structured data nowhere and needs neither
folder. Shipping them empty is worse than not shipping them: a module nobody calls reads as a layer
in use, and the next reader spends time working out how it fits.

Add both when you have a step where there is nothing to explore - the inputs are already on disk and
the output is a fixed shape, so the model needs a prompt and a schema rather than tools.

Two rules the layer lives by:

- **A chain imports nothing from `graph/`** and runs with no graph constructed. That is what keeps
  its tests out of end-to-end territory.
- **Every `with_structured_output` call passes `method=` from per-provider config and guards a
  `None` return.** `None` without an exception was observed once in ~12 calls against a local model;
  a node that assumes an object writes nothing and reports success.

Keep the schema flat and shallow. The more nesting it has, the more provider-specific its handling
becomes - and a `Literal` the model must choose from fails at parse time with a message the retry
can actually use:

```python
class ItemRecord(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", description="kebab-case, unique")
    source: str = Field(description="repo-relative path to the declaring file")


class ItemBatch(BaseModel):
    items: list[ItemRecord]
```
