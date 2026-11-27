"""Release test set 2's 38c3 talk 797: phase 2's redundancy filter model (`frames.redundancyModel`, 3.7 GB on LM
Studio) was asked with the 24-hour unload timer every call sends, stayed loaded beside phase 3's 35B model, memory ran
out and phase 3's first batch of 4 frames ran over an hour (DEFECTS 473). Built from this machine's own default
settings, with no model call: the request the filter's model is sent, against the one phase 3's model is sent."""

from langchain_core.messages import HumanMessage

from support import harness

import config
from services import chat_model_service
from services import frame_redundancy_service


def test_the_configured_filter_model_is_unloaded_soon_after_phase_2_defect():
    # The filter's model was sent ttl 86400 and stayed loaded through phase 3. Now it is sent the filter's 300
    name = config.settings.frames.redundancy_model
    if not name or not config.settings.models[name].spec.startswith("lmstudio:"):
        harness.check("the default settings name an LM Studio filter model", False, str(name))
        return
    if chat_model_service.is_phase_model_spec(name, "frames_processing"):
        harness.check("the filter's model is another model than phase 3's", False, name)
        return
    filter_body = frame_redundancy_service.build_filter_model(name)._get_request_payload([HumanMessage("hi")])
    phase_body = chat_model_service.get_model("frames_processing")._get_request_payload([HumanMessage("hi")])
    harness.check("the filter's model is unloaded 5 minutes after its last call, phase 3's kept a day",
                  filter_body["extra_body"]["ttl"] == 300
                  and phase_body["extra_body"]["ttl"] == chat_model_service.LMSTUDIO_LOAD_SECONDS,
                  f"{filter_body['extra_body']} {phase_body['extra_body']}")
