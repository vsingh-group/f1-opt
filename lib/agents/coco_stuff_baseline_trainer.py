from lib.agents.agent import Agent
from lib.datasets.coco_stuff import COCOStuff
from lib.models.seg_net import SegNet
from lib.utils.functional import cross_entropy2d, get_iou
from pathlib import Path
from statistics import mean
from torch.utils.data import DataLoader
from tqdm import tqdm
import torch


class COCOStuffBaselineTrainer(Agent):
    N_CLASSES = 2

    def run(self):
        trainset = COCOStuff(Path(self.config["dataset path"], "train"))
        train_loader = DataLoader(
            dataset=trainset,
            batch_size=self.config["batch size"],
            shuffle=True)

        valset = COCOStuff(Path(self.config["dataset path"], "val"))
        val_loader = DataLoader(
            dataset=valset, batch_size=self.config["batch size"])

        model = SegNet(n_classes=self.N_CLASSES).to(self.device)
        start_epochs = self._load_checkpoint(model)
        optimizer = torch.optim.Adam(
            model.parameters(), lr=self.config["learning rate"])

        for epoch in tqdm(range(start_epochs, self.config["epochs"])):

            model.train()
            total_loss = 0
            for X, Y in tqdm(train_loader):
                X, Y = X.to(self.device), Y.long().to(self.device)
                Y_ = model(X)
                loss = cross_entropy2d(Y_, Y)
                total_loss += loss.item()
                loss.backward()
                optimizer.step()
                optimizer.zero_grad()
            avg_loss = total_loss / len(train_loader)
            self.logger.log("epoch", epoch, "loss", avg_loss)

            model.eval()
            ious = []
            with torch.no_grad():
                for X, Y in val_loader:
                    X, Y = X.to(self.device), Y.long().to(self.device)
                    Y_ = model(X)
                    _, predicted = torch.max(Y_.data, 1)
                    iou = get_iou(predicted, Y)
                    ious.append(iou.item())

            mean_iou = mean(ious)
            self.logger.log("epoch", epoch, "iou", mean_iou)

            self.logger.graph()

            self._save_checkpoint(epoch, model)