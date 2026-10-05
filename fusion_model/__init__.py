"""Isolated RGB + thermal fusion pipeline.

Self-contained: the MulFS-CAP fusion network, the QS-YOLOv8 fused-image
detector and their weights all live under this package, separate from the
``model/`` folder that holds the RGB-only QS-YOLOv8s detector. Importing this
package does not touch the RGB detector's custom layers.
"""
