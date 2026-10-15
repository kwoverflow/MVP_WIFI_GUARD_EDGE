# In-house temporal segmentation runtime

This service package integrates the reviewed `InhouseSegmentationRealtime` artifact with
the WIFI-GUARD signal contract. The original package SHA-256 and provenance are recorded by
`tools/import_segmentation_package.py`; model weights and datasets remain ignored artifacts.

The live adapter resamples each three-second `(960, 30)` window from 320 Hz to the model's
native 500 samples at 166.6667 Hz, then runs the supplied S3 + PCA-ACF implementation.
Inference returns the linearly interpolated exact-center probability and can apply supplied
causal A or B trigger state per device.
