from utils import AudioUtils
import argparse
import librosa
import numpy as np
import os
import time
import contextlib
from multiprocessing import Pool
from typing import Tuple, List
from nptyping import Array
from pathlib import Path


def generate_feat(
    ftype: str,
    audio_data: Array[float],
    sample_rate: int,
    win_t: float,
    hop_t: float,
    n_mels: int,
) -> Array[float]:
    """Generates the features for an audio sample

    Args:
        ftype:       Type of computed feature
        audio_data:  Input audio sample
        sample_rate: Audio sample rate
        win_t:       FFT window size in seconds
        hop_t:       Frame spacing in seconds
        n_mels:      Number of filter banks if using 'fbank' as the computed feature

    """
    if ftype == "fbank":
        feat = np.transpose(
            AudioUtils.to_melspec(
                audio_data,
                sample_rate,
                int(sample_rate * win_t),
                hop_t,
                win_t,
                n_mels=n_mels,
            )
        )
    else:
        feat = np.transpose(
            AudioUtils.rstft(
                audio_data, sample_rate, int(sample_rate * win_t), hop_t, win_t
            )
        )
    return feat


def prepare_numpy(
    dataset: str,
    set_name: str,
    dataset_dir: str,
    output_dir: str = None,
    ftype: str = "fbank",
    sample_rate: int = None,
    win_t: float = 0.025,
    hop_t: float = 0.010,
    n_mels: int = 80,
) -> Tuple[int, Tuple[Path, Path, Path]]:
    """Handles Numpy format feature and script file generation and saving

    If dataset_dir does not exist, an error will be raised.

    Args:
        dataset:     Name of the dataset for which features are generated
        set_name:    Name of the set (train, dev, test) to operate on
        dataset_dir: Directory containing subdirectories with wav.scp files
        output_dir:  Directory to write to
        ftype:       Type of computed feature
        sample_rate: Sample rate for resampling if not None
        win_t:       FFT window size in seconds
        hop_t:       Frame spacing in seconds
        n_mels:      Number of filter banks if using 'fbank' as the computed feature

    """

    if output_dir is not None:
        set_path = Path(output_dir) / set_name
    else:
        set_path = Path(dataset_dir) / set_name

    file_paths = []
    for name in ("wav.scp", "feats.scp", "len.scp"):
        file_paths.append(set_path / name)

    if not os.path.exists(file_paths[0]):
        raise ValueError(f"The wav.scp file at {file_paths[0]} does not exist!")

    os.makedirs(set_path, exist_ok=True)

    wav_path, feat_path, len_path = file_paths

    start_time = time.time()
    count = 0

    # Opening multiple files at once with context manager
    with contextlib.ExitStack() as stack:
        wavfile = stack.enter_context(open(wav_path))
        featfile, lenfile = [
            stack.enter_context(open(f, "w")) for f in [feat_path, len_path]
        ]
        for i, line in enumerate(wavfile):
            seq, path = line.rstrip().split()
            y, _sr = librosa.load(path, sample_rate, mono=True)
            if sample_rate is None:
                sample_rate = _sr
            elif sample_rate != _sr:
                raise ValueError(f"Inconsistent sample rate ({sample_rate} != {_sr}.")

            feat = generate_feat(ftype, y, sample_rate, win_t, hop_t, n_mels)
            np_path = os.path.join(set_path, f"{seq}.npy")
            with open(np_path, "wb") as numpyfile:
                np.save(numpyfile, feat)
            featfile.write(f"{seq} {np_path}\n")
            lenfile.write(f"{seq} {len(feat)}\n")
            count = i + 1
            if (count) % 1000 == 0:
                print(
                    f"{count} {set_name} files in {time.time() - start_time} seconds."
                )

    print(
        f"Processed {count} files in {set_name} set over {time.time() - start_time} seconds."
    )
    return count, (wav_path, feat_path, len_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "dataset_dir",
        type=str,
        help="Directory containing subdirectories with wav.scp files",
    )
    parser.add_argument(
        "--np_dir", type=str, default=None, help="Output directory for numpy matrices"
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default="librispech",
        choices=["librispeech", "timit"],
        help="Dataset name",
    )
    parser.add_argument(
        "--set_name",
        type=str,
        default=None,
        help="Set {train, dev, test} to operate on, Leave blank for all three",
    )
    parser.add_argument(
        "--ftype",
        type=str,
        default="fbank",
        choices=["fbank", "spec"],
        help="Feature type to compute",
    )
    parser.add_argument(
        "--sr",
        type=int,
        default=None,
        help="Resample raw audio to specified value if not None",
    )
    parser.add_argument(
        "--win_t", type=float, default=0.025, help="Window size in seconds"
    )
    parser.add_argument(
        "--hop_t", type=float, default=0.010, help="Frame spacing in seconds"
    )
    parser.add_argument(
        "--n_mels",
        type=int,
        default=80,
        help="Number of filter banks if choosing fbank",
    )
    args = parser.parse_args()
    print(args)

    func_args = [
        args.dataset,
        args.set_name,
        args.dataset_dir,
        args.np_dir,
        args.ftype,
        args.sr,
        args.win_t,
        args.hop_t,
        args.n_mels,
    ]
    # Parallel run if set_name is unspecified
    if args.set_name is None:
        starmap_args = []
        for s in ["train", "dev", "test"]:
            func_args[1] = s
            starmap_args.append(tuple(func_args))

        files_start_time = time.time()
        with Pool(3) as p:
            results = p.starmap(prepare_numpy, starmap_args)

        print(
            f"Processed {sum(r[0] for r in results)} files in {time.time() - files_start_time} seconds."
        )
    else:
        prepare_numpy(*func_args)
