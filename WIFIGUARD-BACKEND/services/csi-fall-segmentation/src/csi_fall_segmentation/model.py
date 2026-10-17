from __future__ import annotations

import torch
import torch.nn.functional as F
from torch import nn


RESNET_BLOCKS = {"resnet18": (2, 2, 2, 2), "resnet34": (3, 4, 6, 3)}


class BasicBlock(nn.Module):
    expansion = 1

    def __init__(
        self,
        inplanes: int,
        planes: int,
        stride: int = 1,
        downsample: nn.Module | None = None,
    ) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(
            inplanes, planes, kernel_size=3, stride=stride, padding=1, bias=False
        )
        self.bn1 = nn.BatchNorm2d(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = nn.Conv2d(
            planes, planes, kernel_size=3, stride=1, padding=1, bias=False
        )
        self.bn2 = nn.BatchNorm2d(planes)
        self.downsample = downsample

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        identity = x
        out = self.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        if self.downsample is not None:
            identity = self.downsample(x)
        return self.relu(out + identity)


class ResNetImageEncoder(nn.Module):
    def __init__(self, backbone: str, embedding_dim: int, dropout: float) -> None:
        super().__init__()
        if backbone not in RESNET_BLOCKS:
            raise ValueError(f"Unsupported backbone: {backbone}")
        self.backbone = backbone
        self.inplanes = 64
        blocks = RESNET_BLOCKS[backbone]
        self.conv1 = nn.Conv2d(3, 64, kernel_size=7, stride=2, padding=3, bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        self.relu = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        self.layer1 = self._make_layer(64, blocks[0])
        self.layer2 = self._make_layer(128, blocks[1], stride=2)
        self.layer3 = self._make_layer(256, blocks[2], stride=2)
        self.layer4 = self._make_layer(512, blocks[3], stride=2)
        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        self.projection = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(512, embedding_dim),
            nn.LayerNorm(embedding_dim),
            nn.ReLU(inplace=True),
        )

    def _make_layer(self, planes: int, blocks: int, stride: int = 1) -> nn.Sequential:
        downsample = None
        if stride != 1 or self.inplanes != planes:
            downsample = nn.Sequential(
                nn.Conv2d(self.inplanes, planes, kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm2d(planes),
            )
        layers: list[nn.Module] = [BasicBlock(self.inplanes, planes, stride, downsample)]
        self.inplanes = planes
        for _ in range(1, blocks):
            layers.append(BasicBlock(self.inplanes, planes))
        return nn.Sequential(*layers)


def prepare_resnet_image(x: torch.Tensor, image_size: int) -> torch.Tensor:
    if x.ndim != 4:
        raise ValueError(f"Expected BCHW tensor, got {tuple(x.shape)}")
    if x.shape[1] == 1:
        x = x.repeat(1, 3, 1, 1)
    elif x.shape[1] == 2:
        x = torch.cat([x, x.mean(dim=1, keepdim=True)], dim=1)
    elif x.shape[1] > 3:
        x = x[:, :3]
    if x.shape[-2:] != (image_size, image_size):
        x = F.interpolate(
            x, size=(image_size, image_size), mode="bilinear", align_corners=False
        )
    return x


class TemporalPyramidProjector(nn.Module):
    def __init__(self, pyramid_channels: int, output_bins: int) -> None:
        super().__init__()
        self.output_bins = int(output_bins)
        self.lateral = nn.ModuleList(
            [
                nn.Conv1d(channels, pyramid_channels, kernel_size=1)
                for channels in (64, 128, 256, 512)
            ]
        )
        total = pyramid_channels * len(self.lateral)
        self.fuse = nn.Sequential(
            nn.Conv1d(total, total, kernel_size=3, padding=1, bias=False),
            nn.GroupNorm(8, total),
            nn.ReLU(inplace=True),
        )

    def forward(self, features: list[torch.Tensor]) -> torch.Tensor:
        projected: list[torch.Tensor] = []
        for feature, lateral in zip(features, self.lateral, strict=True):
            temporal = lateral(feature.mean(dim=2))
            temporal = F.interpolate(
                temporal, size=self.output_bins, mode="linear", align_corners=False
            )
            projected.append(temporal)
        return self.fuse(torch.cat(projected, dim=1))


class TemporalResidualBlock(nn.Module):
    def __init__(self, channels: int, dilation: int, dropout: float) -> None:
        super().__init__()
        self.block = nn.Sequential(
            nn.Conv1d(
                channels,
                channels,
                kernel_size=3,
                padding=dilation,
                dilation=dilation,
                bias=False,
            ),
            nn.GroupNorm(8, channels),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Conv1d(channels, channels, kernel_size=1, bias=False),
            nn.GroupNorm(8, channels),
        )
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.relu(x + self.block(x))


class DualBranchTemporalSegmentationModel(nn.Module):
    def __init__(
        self,
        backbone: str,
        embedding_dim: int,
        source_dropout: float,
        output_bins: int,
        pyramid_channels: int,
        decoder_channels: int,
        decoder_dilations: list[int],
        dropout: float,
    ) -> None:
        super().__init__()
        self.output_bins = int(output_bins)
        self.encoder_a = ResNetImageEncoder(backbone, embedding_dim, source_dropout)
        self.encoder_b = ResNetImageEncoder(backbone, embedding_dim, source_dropout)
        self.pyramid_a = TemporalPyramidProjector(pyramid_channels, output_bins)
        self.pyramid_b = TemporalPyramidProjector(pyramid_channels, output_bins)
        branch_channels = pyramid_channels * 4
        self.decoder_input = nn.Sequential(
            nn.Conv1d(branch_channels * 2, decoder_channels, kernel_size=1, bias=False),
            nn.GroupNorm(8, decoder_channels),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
        )
        self.decoder = nn.Sequential(
            *[
                TemporalResidualBlock(decoder_channels, dilation, dropout)
                for dilation in decoder_dilations
            ]
        )
        self.segmentation_head = nn.Conv1d(decoder_channels, 1, kernel_size=1)

    @staticmethod
    def feature_pyramid(
        encoder: ResNetImageEncoder, x: torch.Tensor
    ) -> list[torch.Tensor]:
        x = encoder.maxpool(encoder.relu(encoder.bn1(encoder.conv1(x))))
        layer1 = encoder.layer1(x)
        layer2 = encoder.layer2(layer1)
        layer3 = encoder.layer3(layer2)
        layer4 = encoder.layer4(layer3)
        return [layer1, layer2, layer3, layer4]

    def forward(self, s3: torch.Tensor, acf: torch.Tensor, image_size: int) -> torch.Tensor:
        s3_image = prepare_resnet_image(s3, image_size)
        acf_image = prepare_resnet_image(acf, image_size)
        temporal_a = self.pyramid_a(self.feature_pyramid(self.encoder_a, s3_image))
        temporal_b = self.pyramid_b(self.feature_pyramid(self.encoder_b, acf_image))
        fused = self.decoder_input(torch.cat([temporal_a, temporal_b], dim=1))
        return self.segmentation_head(self.decoder(fused)).squeeze(1)


def build_model(checkpoint: dict[str, object]) -> DualBranchTemporalSegmentationModel:
    config = checkpoint["model_config"]
    if not isinstance(config, dict):
        raise ValueError("model_config is missing from checkpoint")
    return DualBranchTemporalSegmentationModel(
        backbone=str(checkpoint.get("backbone", "resnet18")),
        embedding_dim=int(checkpoint.get("embedding_dim", 512)),
        source_dropout=float(checkpoint.get("source_dropout", 0.3)),
        output_bins=int(config["output_bins"]),
        pyramid_channels=int(config["pyramid_channels"]),
        decoder_channels=int(config["decoder_channels"]),
        decoder_dilations=[int(value) for value in config["decoder_dilations"]],
        dropout=float(config["dropout"]),
    )
