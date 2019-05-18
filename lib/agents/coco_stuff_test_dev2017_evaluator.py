from lib.agents.agent import Agent
from lib.datasets.coco_stuff import COCOStuffEval
import importlib
import torch

# from pycocotools.cocostuffhelper import segmentationToCocoResult


class COCOStuffTestDev2017Evaluator(Agent):
    N_CLASSES = 92

    def run(self):
        testset = COCOStuffEval(self.config["dataset path"])

        net_module = importlib.import_module(
            ("lib.models.{}".format(self.config["model"])))
        net = getattr(net_module, "build_" + self.config["model"])

        model = net(
            n_classes=self.N_CLASSES,
            size=(self.config["img width"],
                  self.config["img height"])).to(self.device)
        self._load_checkpoint(model)

        model.eval()
        with torch.no_grad():
            for X in testset:
                X = X.to(self.device)
                Y_, _ = model(X)
                _, predicted = torch.max(Y_.data, 1)