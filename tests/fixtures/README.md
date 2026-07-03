# Test fixtures

## `test_de_hallo_welt.wav`

Not committed. Record yourself saying "Hallo Welt." into this file
(mono, 16 kHz, 16-bit PCM) if you want to run the integration test:

```powershell
python -c "import sounddevice as sd, scipy.io.wavfile as w; import numpy as np; a = sd.rec(int(2*16000), samplerate=16000, channels=1, dtype='int16'); sd.wait(); w.write('test_de_hallo_welt.wav', 16000, a)"
```

Then run:

```
pytest -m integration
```

Skipped by default so CI / dev laptops don't need a working CUDA + model
download.
