# Third-Party Components for the FireRed VAD Integration

This project bundles the following third-party components for voice activity
detection. The full license texts are stored in this directory.

## FireRedVAD

- Upstream: https://github.com/FireRedTeam/FireRedVAD
- Model: https://huggingface.co/FireRedTeam/FireRedVAD
- Copyright 2026 Xiaohongshu.
- License: Apache License 2.0 (see `Apache-2.0.txt`).
- Bundled files:
  - `local_inference/models/firered_vad/Stream-VAD/fireredvad_stream_vad_with_cache.onnx`
  - `local_inference/models/firered_vad/Stream-VAD/cmvn.ark`
- The ONNX model and CMVN statistics are the official files published by the
  FireRedVAD project and are bundled unmodified. This project only adds its own
  inference wrapper; it does not modify the model files.

## kaldi-native-fbank

- Upstream: https://github.com/csukuangfj/kaldi-native-fbank
- Copyright 2009-2012 Karel Vesely; 2016 Johns Hopkins University (author:
  Daniel Povey); 2022 Xiaomi Corporation (authors: Fangjun Kuang).
- License: Apache License 2.0 (see `Apache-2.0.txt`).
- Portions of this library are copied or modified from Kaldi
  (https://github.com/kaldi-asr/kaldi), which is also licensed under
  Apache License 2.0.
- Used unmodified as a Python extension for Kaldi-compatible fbank features.

## ONNX Runtime

- Upstream: https://github.com/microsoft/onnxruntime
- Copyright (c) Microsoft Corporation.
- License: MIT (see `MIT-ONNXRuntime.txt`).
- Used unmodified to run the bundled FireRedVAD ONNX model.
