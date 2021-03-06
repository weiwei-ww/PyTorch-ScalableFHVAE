import librosa
from collections import defaultdict
import numpy as np
from nptyping import Array
from pathlib import Path
import torch
from simple_fhvae import SimpleFHVAE
from fhvae import FHVAE
import shutil
from typing import Optional
import pickle


def check_best(val_lower_bound, best_val_lb) -> bool:
    if torch.mean(val_lower_bound) > best_val_lb:
        return True
    return False


def create_training_strings(args):
    base_string = create_output_dir_name(args.dataset, args.data_format, args.feat_type)
    if args.legacy:
        exp_string = f"{args.model_type}_e{args.epochs}_s{args.steps_per_epoch}_p{args.patience}_a{args.alpha_dis}_legacy"
    else:
        exp_string = (
            f"{args.model_type}_e{args.epochs}_p{args.patience}_a{args.alpha_dis}"
        )
    run_id = f"{base_string}_{exp_string}"
    return base_string, exp_string, run_id


def create_output_dir_name(dataset: str, data_format: str, feat_type: str) -> Path:
    """Concatenates the dataset name, format, and feature type to create a dir name"""
    if data_format.lower() == "numpy":
        dataset += "_np"
    else:
        dataset += "_kd"

    # Kaldi only computes fbank features
    feat_type = "fbank" if data_format == "kaldi" else feat_type

    return Path(dataset + f"_{feat_type}")


def estimate_mu2_dict(model, loader, num_seqs):
    """Estimate mu2 for sequences"""
    model.eval()
    nseg_table = defaultdict(float)
    z2_sum_table = defaultdict(float)
    for _, (idxs, features, nsegs) in enumerate(loader):
        model(features, idxs, len(loader.dataset), nsegs)
        z2 = model.qz2_x[0]
        for _y, _z2 in zip(idxs, z2):
            z2_sum_table[_y] += _z2
            nseg_table[_y] += 1
    mu2_dict = dict()
    for _y in nseg_table:
        r = np.exp(model.pz2[1]) / np.exp(model.pmu2[1])
        mu2_dict[_y] = z2_sum_table[_y] / (nseg_table[_y] + r)
    return mu2_dict


def load_checkpoint_file(checkpoint_file, finetune):
    optim_state = None
    start_epoch = None
    best_val_lb = None
    summary_list = None
    values = None

    strict_mode = True
    checkpoint = torch.load(checkpoint_file)
    model_type = checkpoint["model_type"]
    model_params = checkpoint["model_params"]
    if model_type == "fhvae":
        model = FHVAE(*model_params)
    elif model_type == "simple_fhvae":
        model = SimpleFHVAE(*model_params)
    else:
        # Fallback just in case
        print(f"NON-STANDARD MODEL TYPE {model_type} DETECTED")
        model = model_type
        strict_mode = False
    model.load_state_dict(checkpoint["state_dict"], strict=strict_mode)

    # We don't want to restart training if this is the case
    if not finetune:
        optim_state = checkpoint["optimizer"]
        # Checkpoint was saved at the end of an epoch, start from next epoch
        start_epoch = checkpoint["epoch"] + 1
        best_val_lb = checkpoint["best_val_lb"]
        summary_list = checkpoint["summary_vals"]
        start_epoch += 1
        values = checkpoint["values"]

    return (
        model,
        values,
        optim_state,
        start_epoch,
        best_val_lb,
        summary_list,
    )


def save_args(exp_dir, args):
    with open(f"{exp_dir}/args.pkl", "wb") as f:
        pickle.dump(args, f)


def load_args(exp_dir):
    with open(f"{exp_dir}/args.pkl", "rb") as f:
        args = pickle.load(f)
    return args


def save_checkpoint(
    model,
    optimizer,
    summary_list,
    values_dict,
    run_info: str,
    epoch: int,
    best_epoch: int,
    val_lower_bound: float,
    best_val_lb: float,
    checkpoint_dir: str,
) -> None:
    """Saves checkpoint files"""

    checkpoint = {
        "best_val_lb": best_val_lb,
        "best_epoch": best_epoch,
        "epoch": epoch,
        "model_type": model.model,
        "model_params": (
            model.z1_hus,
            model.z2_hus,
            model.z1_dim,
            model.z2_dim,
            model.x_hus,
        ),
        "optimizer": optimizer.state_dict(),
        "state_dict": model.state_dict(),
        "summary_vals": summary_list,
        "values": values_dict,
    }

    f_str = f"{model.model}_{run_info}_e{epoch}"
    f_path = Path(checkpoint_dir) / f"{f_str}.tar"
    torch.save(checkpoint, f_path)
    if best_epoch == epoch:
        shutil.copyfile(f_path, Path(checkpoint_dir) / f"best_model_{f_str}.tar")


class AudioUtils:
    @staticmethod
    def stft(
        y: Array[float],
        sr: int,
        n_fft: int = 400,
        hop_t: float = 0.010,
        win_t: float = 0.025,
        window: str = "hamming",
        preemphasis: float = 0.97,
    ) -> Array[float]:
        """Short time Fourier Transform

        Args:
            y:           Raw waveform of shape (T,)
            sr:          Sample rate
            n_fft:       Length of the FFT window
            hop_t:       Spacing (in seconds) between consecutive frames
            win_t:       Window size (in seconds)
            window:      Type of window applied for STFT
            preemphasis: Pre-emphasize raw signal with y[t] = x[t] - r*x[t-1]

        Returns:
            (n_fft / 2 + 1, N) matrix; N is number of frames

        """
        if preemphasis > 1e-12:
            y = y - preemphasis * np.concatenate([[0], y[:-1]], 0)
        hop_length = int(sr * hop_t)
        win_length = int(sr * win_t)
        return librosa.core.stft(
            y, n_fft=n_fft, hop_length=hop_length, win_length=win_length, window=window
        )

    @staticmethod
    def rstft(
        y: Array[float],
        sr: int,
        n_fft: int = 400,
        hop_t: float = 0.010,
        win_t: float = 0.025,
        window: str = "hamming",
        preemphasis: float = 0.97,
        log: bool = True,
        log_floor: int = -50,
    ) -> Array[float]:
        """Computes (log) magnitude spectrogram

        Args:
            y:           Raw waveform of shape (T,)
            sr:          Sample rate
            n_fft:       Length of the FFT window
            hop_t:       Spacing (in seconds) between consecutive frames
            win_t:       Window size (in seconds)
            window:      Type of window applied for STFT
            preemphasis: Pre-emphasize raw signal with y[t] = x[t] - r*x[t-1]
            log:         If True, uses log magnitude
            log_floor:   Floor value for log scaling of magnitude

        Returns:
            (n_fft / 2 + 1, N) matrix; N is number of frames

        """
        spec = AudioUtils.stft(y, sr, n_fft, hop_t, win_t, window, preemphasis)
        spec = np.abs(spec)
        if log:
            spec = np.log(spec)
            spec[spec < log_floor] = log_floor
        return spec

    @staticmethod
    def to_melspec(
        y: Array[float, 1],
        sr: int,
        n_fft: int = 400,
        hop_t: float = 0.010,
        win_t: float = 0.025,
        window: str = "hamming",
        preemphasis: float = 0.97,
        n_mels: int = 80,
        log: bool = True,
        norm_mel: str = "slaney",
        log_floor: int = -20,
    ) -> Array[float]:
        """Compute Mel-scale filter bank coefficients:

        Args:
            y:           Numpy array of audio sample
            sr:          Sample rate
            hop_t:       Spacing (in seconds) between consecutive frames
            win_t:       Window size (in seconds)
            window:      Type of window applied for STFT
            preemphasis: Pre-emphasize raw signal with y[t] = x[t] - r*x[t-1]
            n_mels:      Number of filter banks, which are equally spaced in Mel-scale
            log:         If True, use log magnitude
            norm_mel:    Normalize each filter bank to have area of 1 if True;
                             otherwise the peak value of each filter bank is 1

        Returns:
            (n_mels, N) matrix; N is number of frames

        """
        spec = AudioUtils.rstft(
            y, sr, n_fft, hop_t, win_t, window, preemphasis, log=False
        )
        hop_length = int(sr * hop_t)
        melspec = librosa.feature.melspectrogram(
            sr=sr,
            S=spec,
            n_fft=n_fft,
            hop_length=hop_length,
            n_mels=n_mels,
            norm=norm_mel,
        )
        if log:
            melspec = np.log(melspec)
            melspec[melspec < log_floor] = log_floor
        return melspec

    @staticmethod
    def energy_vad(
        y: Array[float],
        sr: int,
        hop_t: float = 0.010,
        win_t: float = 0.025,
        th_ratio: float = 1.04 / 2,
    ) -> Array[bool]:
        """Compute energy-based VAD (Voice activity detection)

        Args:
            y:        Numpy array of audio sample
            sr:       Sample rate
            hop_t:    Spacing (in seconds) between consecutive frames
            win_t:    Window size (in seconds)
            th_ratio: Energy threshold ratio for detection

        Returns:
            Boolean vector indicating whether voice was detected

        """
        hop_length = int(sr * hop_t)
        win_length = int(sr * win_t)
        e = librosa.feature.rmse(y, frame_length=win_length, hop_length=hop_length)
        th = th_ratio * np.mean(e)
        vad = np.asarray(e > th, dtype=int)
        return vad
