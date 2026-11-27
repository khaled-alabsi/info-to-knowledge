"""Validators: pure functions over bytes on disk.

Every validator here has no model, no network and no import from `graph/`. That is what makes them
testable with no model host.

Nothing is re-exported from this file. A caller reaches a validator through its own module -
`from validators import item_validator`, then `item_validator.validate_item(...)` - so the layer
travels with the call instead of living in an import block.
"""
