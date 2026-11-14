"""Core logic: capture, imaging, detection and the person database.

Importing this package wires up the detector registry: :mod:`core.detect`
registers the Ultralytics detector, then :mod:`core.fusion_detect` adds the
isolated RGB + thermal fusion detector and :mod:`core.openvino_detect` adds the
OpenVINO CPU / iGPU detector. Importing them in this order (via the package
initialiser) avoids the circular import between them.
"""

from . import detect  # noqa: F401
from . import fusion_detect  # noqa: F401
from . import openvino_detect  # noqa: F401
