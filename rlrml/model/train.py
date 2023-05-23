import torch
import logging

from .. import load
from . import build


logger = logging.getLogger(__name__)


def log_epoch_start(_trainer, epoch):
    pass


def gpu_memory_remaining():
    allocated = torch.cuda.memory_allocated()
    reserved = torch.cuda.memory_reserved()
    return reserved - allocated


def log_batch_finish(_trainer, epoch, losses, loss, X, y_pred, y, **kwargs):
    logger.info(f"Epoch {epoch} finished with {loss:,}, gpu_free: {gpu_memory_remaining()}")


class ReplayModelManager:

    @classmethod
    def from_dataset(cls, dataset, model=None, *args, **kwargs):
        model = model or build.ReplayModel(dataset.header_info, dataset.playlist)
        data_loader = load.batched_packed_loader(dataset)
        return cls(model, data_loader, *args, **kwargs)

    def __init__(
            self, model, data_loader: torch.utils.data.DataLoader,
            use_cuda=None, on_epoch_start=log_epoch_start,
            on_epoch_finish=log_batch_finish, loss_function=None, accumulation_steps=1,
            lr=.00001, device=None
    ):
        self._device = device or torch.device("cuda")
        self._model = model.to(self._device)
        self._data_loader = data_loader
        self._loss_function = loss_function or torch.nn.MSELoss()
        self._optimizer = torch.optim.Adam(self._model.parameters(), lr=lr)
        self._on_epoch_start = on_epoch_start
        self._on_epoch_finish = on_epoch_finish
        self._accumulation_steps = accumulation_steps

    def train(self, epochs=10):
        logger.info(f"Starting training for {epochs} epochs on {self._device}")
        batch_iterator = iter(self._data_loader)
        for epoch in range(epochs):

            try:
                training_data = next(batch_iterator)
            except StopIteration:
                batch_iterator = iter(self._data_loader)
                training_data = next(batch_iterator)
            self._on_epoch_start(self, epoch)
            X, y, mask = (
                training_data.X.to(self._device),
                training_data.y.to(self._device),
                training_data.mask.to(self._device)
            )
            y_pred = self._model(X)
            loss = self._loss_function(y_pred, y)
            masked_loss = loss * mask
            mean_loss = masked_loss.sum() / mask.sum()
            mean_loss.backward()
            if (epoch + 1) % self._accumulation_steps == 0:
                self._optimizer.step()
                self._optimizer.zero_grad()
                self._on_epoch_finish(
                    trainer=self, epoch=epoch, loss=mean_loss, X=X, y_pred=y_pred, y=y,
                    meta=[training_data.uuids]
                )

    def get_total_loss(self):
        losses = []
        for batch_number, (X, y) in enumerate(self._data_loader):
            X = X.to(self._device)
            y = y.to(self._device)
            y_pred = self._model(X)
            loss = self._loss_function(y_pred, y)
            loss = float(loss)
            losses.append((loss, len(y)))
            self._on_epoch_finish(
                trainer=self, epoch=batch_number, losses=losses, loss=loss, X=X, y_pred=y_pred, y=y
            )

        total_samples_counted = sum(count for _, count in losses)
        weighted_loss = sum(loss * count for loss, count in losses) / total_samples_counted
        return weighted_loss
