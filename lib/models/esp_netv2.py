# ============================================
__author__ = "Sachin Mehta"
__license__ = "MIT"
__maintainer__ = "Sachin Mehta"
# ============================================

from cnn.Model import EESPNet, EESP
from torch import nn
import os
import torch
import torch.nn.functional as F


class EESPNet_Seg(nn.Module):
    def __init__(self, classes=20, s=1, pretrained=None, gpus=1):
        super().__init__()
        classificationNet = EESPNet(classes=1000, s=s)
        if gpus >= 1:
            classificationNet = nn.DataParallel(classificationNet)
        # load the pretrained weights
        if pretrained:
            if not os.path.isfile(pretrained):
                print(
                    'Weight file does not exist. Training without pre-trained'
                    'weights')
            print('Model initialized with pretrained weights')
            classificationNet.load_state_dict(torch.load(pretrained))

        self.net = classificationNet.module

        del classificationNet
        # delete last few layers
        del self.net.classifier
        del self.net.level5
        del self.net.level5_0
        if s <= 0.5:
            p = 0.1
        else:
            p = 0.2

        self.proj_L4_C = CBR(self.net.level4[-1].module_act.num_parameters,
                             self.net.level3[-1].module_act.num_parameters, 1,
                             1)
        pspSize = 2 * self.net.level3[-1].module_act.num_parameters
        self.pspMod = nn.Sequential(
            EESP(pspSize, pspSize // 2, stride=1, k=4, r_lim=7),
            PSPModule(pspSize // 2, pspSize // 2))
        self.project_l3 = nn.Sequential(
            nn.Dropout2d(p=p), C(pspSize // 2, classes, 1, 1))
        self.act_l3 = BR(classes)
        self.project_l2 = CBR(self.net.level2_0.act.num_parameters + classes,
                              classes, 1, 1)
        self.project_l1 = nn.Sequential(
            nn.Dropout2d(p=p),
            C(self.net.level1.act.num_parameters + classes, classes, 1, 1))

    def hierarchicalUpsample(self, x, factor=3):
        for i in range(factor):
            x = F.interpolate(
                x, scale_factor=2, mode='bilinear', align_corners=True)
        return x

    def forward(self, input):
        out_l1, out_l2, out_l3, out_l4 = self.net(input, seg=True)
        out_l4_proj = self.proj_L4_C(out_l4)
        up_l4_to_l3 = F.interpolate(
            out_l4_proj, scale_factor=2, mode='bilinear', align_corners=True)
        merged_l3_upl4 = self.pspMod(torch.cat([out_l3, up_l4_to_l3], 1))
        proj_merge_l3_bef_act = self.project_l3(merged_l3_upl4)
        proj_merge_l3 = self.act_l3(proj_merge_l3_bef_act)
        out_up_l3 = F.interpolate(
            proj_merge_l3, scale_factor=2, mode='bilinear', align_corners=True)
        merge_l2 = self.project_l2(torch.cat([out_l2, out_up_l3], 1))
        out_up_l2 = F.interpolate(
            merge_l2, scale_factor=2, mode='bilinear', align_corners=True)
        merge_l1 = self.project_l1(torch.cat([out_l1, out_up_l2], 1))
        if self.training:
            return F.interpolate(
                merge_l1, scale_factor=2, mode='bilinear',
                align_corners=True), self.hierarchicalUpsample(
                    proj_merge_l3_bef_act)
        else:
            return F.interpolate(
                merge_l1, scale_factor=2, mode='bilinear', align_corners=True)


class PSPModule(nn.Module):
    def __init__(self, features, out_features=1024, sizes=(1, 2, 4, 8)):
        super().__init__()
        self.stages = []
        self.stages = nn.ModuleList(
            [C(features, features, 3, 1, groups=features) for size in sizes])
        self.project = CBR(features * (len(sizes) + 1), out_features, 1, 1)

    def forward(self, feats):
        h, w = feats.size(2), feats.size(3)
        out = [feats]
        for stage in self.stages:
            feats = F.avg_pool2d(feats, kernel_size=3, stride=2, padding=1)
            upsampled = F.interpolate(
                input=stage(feats),
                size=(h, w),
                mode='bilinear',
                align_corners=True)
            out.append(upsampled)
        return self.project(torch.cat(out, dim=1))


class CBR(nn.Module):
    '''
    This class defines the convolution layer with batch normalization and
     PReLU activation
    '''

    def __init__(self, nIn, nOut, kSize, stride=1, groups=1):
        '''
        :param nIn: number of input channels
        :param nOut: number of output channels
        :param kSize: kernel size
        :param stride: stride rate for down-sampling. Default is 1
        '''
        super().__init__()
        padding = int((kSize - 1) / 2)
        self.conv = nn.Conv2d(
            nIn,
            nOut,
            kSize,
            stride=stride,
            padding=padding,
            bias=False,
            groups=groups)
        self.bn = nn.BatchNorm2d(nOut)
        self.act = nn.PReLU(nOut)

    def forward(self, input):
        '''
        :param input: input feature map
        :return: transformed feature map
        '''
        output = self.conv(input)
        # output = self.conv1(output)
        output = self.bn(output)
        output = self.act(output)
        return output


class BR(nn.Module):
    '''
        This class groups the batch normalization and PReLU activation
    '''

    def __init__(self, nOut):
        '''
        :param nOut: output feature maps
        '''
        super().__init__()
        self.bn = nn.BatchNorm2d(nOut)
        self.act = nn.PReLU(nOut)

    def forward(self, input):
        '''
        :param input: input feature map
        :return: normalized and thresholded feature map
        '''
        output = self.bn(input)
        output = self.act(output)
        return output


class CB(nn.Module):
    '''
       This class groups the convolution and batch normalization
    '''

    def __init__(self, nIn, nOut, kSize, stride=1, groups=1):
        '''
        :param nIn: number of input channels
        :param nOut: number of output channels
        :param kSize: kernel size
        :param stride: optinal stide for down-sampling
        '''
        super().__init__()
        padding = int((kSize - 1) / 2)
        self.conv = nn.Conv2d(
            nIn,
            nOut,
            kSize,
            stride=stride,
            padding=padding,
            bias=False,
            groups=groups)
        self.bn = nn.BatchNorm2d(nOut)

    def forward(self, input):
        '''
        :param input: input feature map
        :return: transformed feature map
        '''
        output = self.conv(input)
        output = self.bn(output)
        return output


class C(nn.Module):
    '''
    This class is for a convolutional layer.
    '''

    def __init__(self, nIn, nOut, kSize, stride=1, groups=1):
        '''
        :param nIn: number of input channels
        :param nOut: number of output channels
        :param kSize: kernel size
        :param stride: optional stride rate for down-sampling
        '''
        super().__init__()
        padding = int((kSize - 1) / 2)
        self.conv = nn.Conv2d(
            nIn,
            nOut,
            kSize,
            stride=stride,
            padding=padding,
            bias=False,
            groups=groups)

    def forward(self, input):
        '''
        :param input: input feature map
        :return: transformed feature map
        '''
        output = self.conv(input)
        return output


class CDilated(nn.Module):
    '''
    This class defines the dilated convolution.
    '''

    def __init__(self, nIn, nOut, kSize, stride=1, d=1, groups=1):
        '''
        :param nIn: number of input channels
        :param nOut: number of output channels
        :param kSize: kernel size
        :param stride: optional stride rate for down-sampling
        :param d: optional dilation rate
        '''
        super().__init__()
        padding = int((kSize - 1) / 2) * d
        self.conv = nn.Conv2d(
            nIn,
            nOut,
            kSize,
            stride=stride,
            padding=padding,
            bias=False,
            dilation=d,
            groups=groups)

    def forward(self, input):
        '''
        :param input: input feature map
        :return: transformed feature map
        '''
        output = self.conv(input)
        return output


class CDilatedB(nn.Module):
    '''
    This class defines the dilated convolution with batch normalization.
    '''

    def __init__(self, nIn, nOut, kSize, stride=1, d=1, groups=1):
        '''
        :param nIn: number of input channels
        :param nOut: number of output channels
        :param kSize: kernel size
        :param stride: optional stride rate for down-sampling
        :param d: optional dilation rate
        '''
        super().__init__()
        padding = int((kSize - 1) / 2) * d
        self.conv = nn.Conv2d(
            nIn,
            nOut,
            kSize,
            stride=stride,
            padding=padding,
            bias=False,
            dilation=d,
            groups=groups)
        self.bn = nn.BatchNorm2d(nOut)

    def forward(self, input):
        '''
        :param input: input feature map
        :return: transformed feature map
        '''
        return self.bn(self.conv(input))


def build_esp_netv2(n_classes=21):
    net = EESPNet_Seg(n_classes=n_classes)
    return net

def build_esp_netv2_f1(n_classes=21):
    raise NotImplementedError