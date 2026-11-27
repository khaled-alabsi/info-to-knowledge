"""Where the Settings page takes the list a setting is chosen from, when the setting's type does not name its values:
a field of `config.py` names one of these in its `json_schema_extra` under `CHOICES_KEY`.
"""

CHOICES_KEY = "choicesFrom"   # the key of a field's `json_schema_extra` that names where its list comes from
MODEL_NAMES = "model-names"   # the names of the configured models (`models`)
HOST_MODELS = "host-models"   # the models the known hosts serve, as a spec (`lmstudio:<id>`)
BASE_URLS = "base-urls"       # the addresses of the hosts the settings files and the profiles name
