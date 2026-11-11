"""The drafts bay — adapters drafted by source_draft live here until the gate admits them.

Deliberately OUTSIDE symbiont/adapters/: the shipped loop refuses at load time when an adapter
has no sources.yml entry, so a draft (which by definition has none yet) must not sit where the
loop walks. source_admit moves a drafted adapter into symbiont/adapters/ in the same breath as
it flips the source active in sources.yml, and the contract's registry check runs after the
move — config and code agree the moment a source goes live.
"""
