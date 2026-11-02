"""CPU placement must disable both GPU devices and context operation offload."""
from types import SimpleNamespace

import pytest

pytest.importorskip("gguf")
from local_inference.vendor.qwen_asr_gguf.inference import llama


@pytest.mark.parametrize("layers", [0, 99, -1])
def test_model_cpu_selection_passes_an_empty_device_list(monkeypatch, tmp_path, layers):
    monkeypatch.setattr(llama, "_resolve_llama_lib_dir", lambda: str(tmp_path))
    monkeypatch.setattr(llama, "init_llama_lib", lambda: None)
    monkeypatch.setattr(llama, "llama_model_default_params", llama.llama_model_params)
    monkeypatch.setenv("PATH", "")
    # The loader normally adds its DLL directory. No native library is needed here.
    monkeypatch.delattr(llama.os, "add_dll_directory", raising=False)
    captured = {}

    def load(_path, params):
        captured["layers"] = params.n_gpu_layers
        # Read the array while the native call owns it; a NULL pointer means
        # automatic GPU discovery, while [NULL] explicitly excludes all GPUs.
        captured["empty_devices"] = bool(params.devices) and params.devices[0] is None
        return 1

    monkeypatch.setattr(llama, "llama_model_load_from_file", load)
    assert llama.load_model(str(tmp_path / "model.gguf"), n_gpu_layers=layers) == 1
    assert captured == {
        "layers": 99 if layers == -1 else layers,
        "empty_devices": layers == 0,
    }


@pytest.mark.parametrize("layers,offload_kqv", [(0, True), (0, False), (99, True), (99, False)])
def test_cpu_context_disables_gpu_compute_and_kv_offload(monkeypatch, layers, offload_kqv):
    def defaults():
        params = llama.llama_context_params()
        params.op_offload = True
        return params

    captured = {}

    def init(_model, params):
        captured.update(kqv=params.offload_kqv, operations=params.op_offload)
        return 1

    monkeypatch.setattr(llama, "llama_context_default_params", defaults)
    monkeypatch.setattr(llama, "llama_init_from_model", init)
    monkeypatch.setattr(llama, "llama_free", lambda _ptr: None)
    ctx = llama.LlamaContext(SimpleNamespace(ptr=1, n_gpu_layers=layers), offload_kqv=offload_kqv)
    assert captured == {
        "kqv": offload_kqv and layers != 0,
        "operations": layers != 0,
    }
    del ctx
