import torch
try:
    import nvdiffrast.torch as dr
    ctx = dr.RasterizeCudaContext()
    print("nvdiffrast OK", torch.__version__)
except Exception as e:
    print("nvdiffrast NG", type(e).__name__, e)
try:
    import xatlas; print("xatlas OK")
except Exception as e:
    print("xatlas NG")
