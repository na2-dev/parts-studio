# アルベドの黒いテクセル（島の中）を数えて、縮小版を書く
import sys, numpy as np
from PIL import Image
alb = np.asarray(Image.open(sys.argv[1]).convert('RGB')).astype(int)
cov = np.asarray(Image.open(sys.argv[2]).convert('L')) > 0
black = (alb.sum(-1) < 30)
print(f'島の中の黒: {(black & cov).sum():,} / {cov.sum():,} = {(black & cov).sum() / cov.sum():.1%}   島の外の黒: {(black & ~cov).sum():,}')
Image.fromarray(alb.astype(np.uint8)).resize((1024, 1024)).save(sys.argv[3])
