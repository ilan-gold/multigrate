from scvi.train import AdversarialTrainingPlan
from scvi import _CONSTANTS


class MultiVAETrainingPlan(AdversarialTrainingPlan):
    def validation_step(self, batch, batch_idx):
        _, _, scvi_loss = self.forward(batch, loss_kwargs=self.loss_kwargs)
        reconstruction_loss = scvi_loss.reconstruction_loss
        self.log("validation_loss", scvi_loss.loss, on_epoch=True)
        val_losses = {
            f"modality_{i}_recon_loss": v
            for i, v in enumerate(scvi_loss.modality_recon_losses)
        }
        val_losses.update(
            {
                "reconstruction_loss_sum": reconstruction_loss.sum(),
                "kl_local_sum": scvi_loss.kl_local.sum(),
                "kl_global": scvi_loss.kl_global,
                "n_obs": reconstruction_loss.shape[0],
                "integ_loss": scvi_loss.integ_loss,
            }
        )
        return val_losses

    def validation_epoch_end(self, outputs):
        """Aggregate validation step information."""
        n_obs, elbo, rec_loss, kl_local, integ = 0, 0, 0, 0, 0
        n_modalities = 0
        modality_losses = []
        for key in outputs[0].keys():
            if key.startswith("modality"):
                n_modalities += 1
                modality_losses.append(0)

        for tensors in outputs:
            elbo += tensors["reconstruction_loss_sum"] + tensors["kl_local_sum"]
            rec_loss += tensors["reconstruction_loss_sum"]
            kl_local += tensors["kl_local_sum"]
            n_obs += tensors["n_obs"]
            integ += tensors["integ_loss"]
            for i in range(n_modalities):
                modality_losses[i] += tensors[f"modality_{i}_recon_loss"]

        # kl global same for each minibatch
        kl_global = outputs[0]["kl_global"]
        elbo += kl_global
        self.log("elbo_validation", elbo / n_obs)
        self.log("reconstruction_loss_validation", rec_loss / n_obs)
        self.log("kl_local_validation", kl_local / n_obs)
        self.log("kl_global_validation", kl_global)
        self.log("integ_validation", integ / n_obs)
        for i, v in enumerate(modality_losses):
            self.log(f"modality_{i}_recon_loss_validation", v / n_obs)

    def training_step(self, batch, batch_idx, optimizer_idx=0):
        kappa = (
            1 - self.kl_weight
            if self.scale_adversarial_loss == "auto"
            else self.scale_adversarial_loss
        )
        batch_tensor = batch[_CONSTANTS.BATCH_KEY]
        if optimizer_idx == 0:
            loss_kwargs = dict(kl_weight=self.kl_weight)
            inference_outputs, _, scvi_loss = self.forward(
                batch, loss_kwargs=loss_kwargs
            )

            loss = scvi_loss.loss
            # fool classifier if doing adversarial training
            if kappa > 0 and self.adversarial_classifier is not False:
                z = inference_outputs["z"]
                fool_loss = self.loss_adversarial_classifier(z, batch_tensor, False)
                loss += fool_loss * kappa

            reconstruction_loss = scvi_loss.reconstruction_loss
            self.log("train_loss", loss, on_epoch=True)
            train_losses = {
                f"modality_{i}_recon_loss": scvi_loss.modality_recon_losses[k].detach()
                for i, k in enumerate(scvi_loss.modality_recon_losses)
            }
            train_losses.update(
                {
                    "loss": loss,
                    "reconstruction_loss_sum": reconstruction_loss.sum().detach(),
                    "kl_local_sum": scvi_loss.kl_local.sum().detach(),
                    "kl_global": scvi_loss.kl_global.detach(),
                    "n_obs": reconstruction_loss.shape[0],
                    "integ_loss": scvi_loss.integ_loss.detach(),
                }
            )
            return train_losses

        # train adversarial classifier
        # this condition will not be met unless self.adversarial_classifier is not False
        if optimizer_idx == 1:
            inference_inputs = self.module._get_inference_input(batch)
            outputs = self.module.inference(**inference_inputs)
            z = outputs["z"]
            loss = self.loss_adversarial_classifier(z.detach(), batch_tensor, True)
            loss *= kappa

            return loss

    def training_epoch_end(self, outputs):
        # only report from optimizer one loss signature
        if self.adversarial_classifier:
            self.training_epoch_end_mil(outputs[0])
        else:
            self.training_epoch_end_mil(outputs)

    def training_epoch_end_mil(self, outputs):
        n_obs, elbo, rec_loss, kl_local, integ = 0, 0, 0, 0, 0
        n_modalities = 0
        modality_losses = []
        for key in outputs[0].keys():
            if key.startswith("modality"):
                n_modalities += 1
                modality_losses.append(0)

        for tensors in outputs:
            elbo += tensors["reconstruction_loss_sum"] + tensors["kl_local_sum"]
            rec_loss += tensors["reconstruction_loss_sum"]
            kl_local += tensors["kl_local_sum"]
            n_obs += tensors["n_obs"]
            integ += tensors["integ_loss"]
            for i in range(n_modalities):
                modality_losses[i] += tensors[f"modality_{i}_recon_loss"]

        # kl global same for each minibatch
        kl_global = outputs[0]["kl_global"]
        elbo += kl_global
        self.log("elbo_train", elbo / n_obs)
        self.log("reconstruction_loss_train", rec_loss / n_obs)
        self.log("kl_local_train", kl_local / n_obs)
        self.log("kl_global_train", kl_global)
        self.log("integ_train", integ / n_obs)
        for i, v in enumerate(modality_losses):
            self.log(f"modality_{i}_recon_loss_train", v / n_obs)
