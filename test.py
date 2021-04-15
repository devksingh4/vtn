import os
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"] = "0,1"

import yaml
from argparse import ArgumentParser, Namespace
import torch
torch.backends.cudnn.benchmark = True

import torch.nn as nn
import numpy as np
from tqdm import tqdm, trange

from model import VTN
from utils.data import UCF101, SMTHV2, Kinetics400
from utils.utils import preprocess
from torchvision import transforms

from torch.utils.data import DataLoader, random_split
from torch.optim import Adam, SGD, Adagrad
from torch.optim.lr_scheduler import ReduceLROnPlateau
from utils import load_yaml
from einops import rearrange

# Parse arguments
parser = ArgumentParser()

parser.add_argument("--annotations", type=str, default="dataset/kinetics-400/annotations.json", help="Dataset labels path")
parser.add_argument("--root-dir", type=str, default="dataset/kinetics-400/val", help="Dataset files root-dir")
parser.add_argument("--classInd", type=str, default="dataset/ucf/annotation/classInd.txt", help="ClassInd file")
parser.add_argument("--classes", type=int, default=400, help="Number of classes")
parser.add_argument("--dataset", choices=['ucf', 'smth', 'kinetics'], default='kinetics', help='Dataset type')
parser.add_argument("--per_sample", type=int, default=4, help="Clips per sample")
parser.add_argument("--weight-path", type=str, default="weights/kinetics/lin-v3/weights_25.pth", help='Path to load weights')
# Hyperparameters
parser.add_argument("--batch-size", type=int, default=64, help="Batch size")
parser.add_argument("--config", type=str, default="configs/lin-vtn.yaml", help="Config file")



# Parse arguments
args = parser.parse_args()
print(args)

# Load config
cfg = load_yaml(args.config)

# Load model
model = VTN(**vars(cfg))

if torch.cuda.is_available():
    model = nn.DataParallel(model).cuda()

model.load_state_dict(torch.load(args.weight_path))
model.eval()


# Load dataset
if args.dataset == 'ucf':
  # Load class name to index
  class_map = {}
  with open(args.classInd, "r") as f:
    for line in f.readlines():
        index, name = line.strip().split()
        index = int(index)
        class_map[name] = index

  dataset = UCF101(args.annotations, args.root_dir, preprocess=preprocess, classes=args.classes, frames=cfg.frames, train=False, class_map=class_map)

elif args.dataset == 'smth':
  dataset = SMTHV2(args.annotations, args.root_dir, preprocess=preprocess, frames=cfg.frames)

elif args.dataset == 'kinetics':
  dataset = Kinetics400(args.annotations, args.root_dir, preprocess=preprocess, frames=cfg.frames, per_sample=args.per_sample)

dataloader = DataLoader(dataset, batch_size=args.batch_size, num_workers=16)

# Loss
loss_func = nn.CrossEntropyLoss()

# Softmax
softmax = nn.LogSoftmax(dim=1)

# Validation
val_loss = 0
top1_acc = 0
top5_acc = 0


for src, target in tqdm(dataloader, desc="Validating"):
    # src, target = train_loader[i]
    if torch.cuda.is_available():
        src = src.cuda()
        target = target.cuda()
    
    with torch.no_grad():
        output = model(src)
        loss = loss_func(output, target)
        val_loss += loss.item()
        
        # Rearrange
        output = torch.sum(rearrange(output, '(b p) d -> b p d', p=args.per_sample), dim=1)
        target = rearrange(target, '(b p) -> b p', p=args.per_sample)[:, 0]

        output = softmax(output)

        # Top 1
        top1_acc += torch.sum(torch.argmax(output, dim=1) == target).cpu().detach().item()
        # Top 5
        _, idx = torch.topk(output, 5, dim=1)
        for label, top5 in zip(target, idx):
          if label in top5:
            top5_acc += 1
        

count = len(dataloader) * args.batch_size / args.per_sample

val_loss = val_loss / len(dataloader)
top1_acc = top1_acc / count
top5_acc = top5_acc / count

print(f'Loss: {val_loss}, Top 1: {top1_acc}, Top 5: {top5_acc}')
