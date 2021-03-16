import argparse
import json
import os
import pandas as pd
import numpy as np
from tqdm import tqdm
import torch
from transformers import RobertaTokenizer, RobertaConfig
from models import RobertaForTokenClassification
from utils import format_punctuatation, seed_everything, split_text, add_to_label


def read_csv(path):
    df = pd.read_csv(path)
    data = []
    for idx, address in df.values.tolist():
        data.append(address)

    return data


def text_to_index(data, tokenizer, max_sequence_length):
    cls_id = 0
    eos_id = 2
    pad_id = 1

    subwords = []
    index = np.zeros((len(data), max_sequence_length))
    for idx, address in tqdm(enumerate(data), total=len(data)):
        address_word = format_punctuatation(address)
        address = " " + " ".join(address_word)
        input_ids = tokenizer(address)['input_ids']
        subword = tokenizer.convert_ids_to_tokens(input_ids)

        if len(input_ids) > max_sequence_length:
            input_ids = input_ids[:max_sequence_length]
            input_ids[-1] = eos_id
        else:
            input_ids = input_ids + [pad_id, ] * (max_sequence_length - len(input_ids))

        index[idx, :] = np.array(input_ids, dtype=np.long)
        subwords.append(subword)

    return index, subwords


def handle_acronyms(dict_acronyms, text):
    words = split_text(text)
    words = [word if word not in dict_acronyms else dict_acronyms[word] for word in words]
    return "".join(words)


def sufprocess(dict_acronyms, data, subwords, preds):
    index = []
    label = []
    for idx in range(len(data)):
        address = data[idx]
        subword = subwords[idx][1: -1]
        pred = preds[idx][1: len(subword) + 1]
        assert len(subword) == len(pred)
        poi_start, poi_end, street_start, street_end = -1, -1, -1, -1
        num_poi, num_street = 0, 0
        j = 0
        for i in range(len(subword)):
            sw = subword[i]
            if sw.startswith("Ġ"):
                sw = sw[1:]

            if pred[i] == 1:
                if num_poi == 0:
                    poi_start = j
                    poi_end = j + len(sw)
                num_poi += 1

            elif pred[i] == 3:
                if num_street == 0:
                    street_start = j
                    street_end = j + len(sw)
                num_street += 1

            elif pred[i] == 2:
                if num_poi == 1:
                    poi_end = j + len(sw)

            elif pred[i] == 4:
                if num_street == 1:
                    street_end = j + len(sw)

            j += len(sw)
            while j < len(address) and address[j] == " ":
                j += 1

        index.append(idx)

        poi, street = "", ""
        if poi_start >= 0:
            poi = add_to_label(address, poi_start, poi_end).strip()
            poi = handle_acronyms(dict_acronyms, poi)

        if street_start >= 0:
            street = add_to_label(address, street_start, street_end).strip()
            street = handle_acronyms(dict_acronyms, street)

        label.append(poi + "/" + street)

        # print("address:", address)
        # print("pred:", pred)
        # if poi_start >= 0:
        #     print("poi:", address[poi_start: poi_end])
        #
        # if street_start >= 0:
        #     print("street:", address[street_start: street_end])
        #
        # print("------------------------------------------------------")

    df = pd.DataFrame(data={'id': index, 'POI/street': label})
    df.to_csv("data/submission.csv", index=False)


def main():
    parser = argparse.ArgumentParser(description='Process some integers.')
    parser.add_argument('--test_path', type=str, default='./data/test.csv')
    parser.add_argument('--dict_acronyms_path', type=str, default='./data/dict_acronyms.json')
    parser.add_argument('--model_name', type=str, default='cahya/roberta-base-indonesian-522M')
    parser.add_argument('--max_sequence_length', type=int, default=64)
    parser.add_argument('--batch_size', type=int, default=16)
    parser.add_argument('--seed', type=int, default=69)
    parser.add_argument('--ckpt_path', type=str, default='./models')

    args = parser.parse_args()

    seed_everything(69)

    with open(args.dict_acronyms_path, "r") as f:
        dict_acronyms = json.load(f)

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    tokenizer = RobertaTokenizer.from_pretrained(args.model_name)
    model_bert = torch.load(os.path.join(args.ckpt_path, "model.pt"))
    model_bert.to(device)
    data = read_csv(args.test_path)
    index, subwords = text_to_index(data, tokenizer, args.max_sequence_length)

    test_dataset = torch.utils.data.TensorDataset((torch.tensor(index, dtype=torch.long)))
    test_loader = torch.utils.data.DataLoader(test_dataset, batch_size=args.batch_size, shuffle=False)

    model_bert.eval()

    preds = []
    pbar = tqdm(enumerate(test_loader), total=len(test_loader), leave=False)
    for i, (x_batch,) in pbar:
        mask = (x_batch != 1)
        with torch.no_grad():
            y_hat = model_bert(x_batch.to(device), attention_mask=mask.to(device))

        y_pred = torch.argmax(y_hat, 2)
        preds += y_pred.detach().cpu().numpy().tolist()

    sufprocess(dict_acronyms, data, subwords, preds)


if __name__ == '__main__':
    main()
