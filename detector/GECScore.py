import logging
import random
import numpy as np
import torch
import tqdm
import argparse
import json
import os
import nltk
from sklearn.metrics import (
    roc_curve, auc, confusion_matrix, precision_score,
    recall_score, accuracy_score, f1_score
)
from rouge import Rouge
from openai import OpenAI

# Initialize OpenAI client
client = OpenAI(api_key="")  # Replace with your OpenAI API key

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# Download NLTK wordnet (if needed)
nltk.download("wordnet")

# Initialize Rouge scoring tool
rouge = Rouge()

def chat_with_gpt4o(prompt, model):
    """
    Interacts with GPT-4o or another LLM to generate text.

    Args:
        prompt (str): Input text to prompt the LLM.
        model (str): The LLM model to use.

    Returns:
        str: The response generated by the LLM.
    """
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": prompt},
            ],
            temperature=0.01,
        )
        return completion.choices[0].message.content
    except Exception as e:
        logging.error(f"Error during LLM interaction: {e}")
        return None


def get_roc_metrics(real_preds, sample_preds):
    """
    Computes ROC metrics, including AUC, optimal threshold, and performance scores.

    Args:
        real_preds (list): Predictions for the human-labeled data.
        sample_preds (list): Predictions for the machine-labeled data.

    Returns:
        tuple: ROC AUC, optimal threshold, confusion matrix, precision, recall, F1 score, and accuracy.
    """
    real_labels = [0] * len(real_preds) + [1] * len(sample_preds)
    predicted_probs = real_preds + sample_preds

    fpr, tpr, thresholds = roc_curve(real_labels, predicted_probs)
    roc_auc = auc(fpr, tpr)

    # Youden's J statistic to find the optimal threshold
    optimal_idx = np.argmax(tpr - fpr)
    optimal_threshold = thresholds[optimal_idx]

    predictions = [1 if prob >= optimal_threshold else 0 for prob in predicted_probs]
    conf_matrix = confusion_matrix(real_labels, predictions)
    precision = precision_score(real_labels, predictions)
    recall = recall_score(real_labels, predictions)
    f1 = f1_score(real_labels, predictions)
    accuracy = accuracy_score(real_labels, predictions)

    return float(roc_auc), float(optimal_threshold), conf_matrix.tolist(), float(precision), float(recall), float(f1), float(accuracy)


def get_roc_metrics_with_threshold(real_preds, sample_preds, threshold):
    """
    Computes ROC metrics with a fixed threshold.

    Args:
        real_preds (list): Predictions for the human-labeled data.
        sample_preds (list): Predictions for the machine-labeled data.
        threshold (float): Predefined threshold for classification.

    Returns:
        tuple: ROC AUC, threshold, confusion matrix, precision, recall, F1 score, and accuracy.
    """
    real_labels = [0] * len(real_preds) + [1] * len(sample_preds)
    predicted_probs = real_preds + sample_preds

    predictions = [1 if prob >= threshold else 0 for prob in predicted_probs]
    fpr, tpr, _ = roc_curve(real_labels, predictions)
    roc_auc = auc(fpr, tpr)

    conf_matrix = confusion_matrix(real_labels, predictions)
    precision = precision_score(real_labels, predictions)
    recall = recall_score(real_labels, predictions)
    f1 = f1_score(real_labels, predictions)
    accuracy = accuracy_score(real_labels, predictions)

    return float(roc_auc), float(threshold), conf_matrix.tolist(), float(precision), float(recall), float(f1), float(accuracy)


def process_data(filename, llm_model):
    """
    Processes the data file to generate predictions and calculate scores.

    Args:
        filename (str): Path to the data file.
        llm_model (str): The LLM model to use.

    Returns:
        dict: Processed data and predictions grouped by label.
    """
    # Check if the processed file already exists
    processed_file = filename.replace(".json", "_processed_train.json")
    if os.path.exists(processed_file):
        logging.info(f"Loading cached file: {processed_file}")
        with open(processed_file, "r") as f:
            data = json.load(f)
    else:
        logging.info(f"Processed file not found. Processing data from {filename}")
        with open(filename, "r") as f:
            data = json.load(f)

    predictions = {'human': [], 'llm': []}  # Separate keys for modes

    for item in tqdm.tqdm(data):
        try:
            text = item["text"]
            if not item.get("gec_text"):
                prompt = f"Correct the grammar errors in the following text: {text}\nCorrected text:"
                gec_text = chat_with_gpt4o(prompt, llm_model)
                item["gec_text"] = gec_text
        except Exception as e:
            logging.error(f"Error processing item: {e}")

        # Calculate Rouge scores
        try:
            if item.get("gec_text"):
                text = item["text"]
                gec_text = item["gec_text"]
                rouge_score = rouge.get_scores(text, gec_text, avg=True)
                item['llm_text_rouge2_score'] = rouge_score['rouge-2']['f']
        except Exception as rouge_error:
            logging.warning(f"Failed to compute Rouge score: {rouge_error}")
            item['llm_text_rouge2_score'] = None

        # Group predictions by label
        if item["label"] == "human":
            predictions["human"].append(item['llm_text_rouge2_score'])
        elif item["label"] == "llm":
            predictions["llm"].append(item['llm_text_rouge2_score'])

    # Save processed data
    with open(processed_file, "w") as f:
        json.dump(data, f, indent=4)

    return predictions


def experiment(args):
    """
    Main experiment pipeline. Processes training and testing data, computes metrics, and saves results.

    Args:
        args: Command-line arguments.
    """
    # Set random seed for reproducibility
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    logging.info("Random seed set for reproducibility.")

    if args.threshold:
        # Process and evaluate test data
        test_filenames = args.test_data_path.split(",")
        for filename in test_filenames:
            test_predictions = process_data(filename, args.llm_model)

            # Evaluate test predictions
            roc_auc_test, _, conf_matrix_test, precision_test, recall_test, f1_test, accuracy_test = get_roc_metrics_with_threshold(
                test_predictions['human'], test_predictions['llm'], args.threshold_value
            )

            # Save results
            output_results_path = filename.replace(".json", "_results_test.json")
            with open(output_results_path, "w") as f:
                json.dump({
                    "roc_auc": roc_auc_test,
                    "optimal_threshold": args.threshold_value,
                    "conf_matrix": conf_matrix_test,
                    "precision": precision_test,
                    "recall": recall_test,
                    "f1": f1_test,
                    "accuracy": accuracy_test,
                }, f, indent=4)
            logging.info(f"Saved results to {output_results_path}")

    else:
        # Combine predictions from all training data to calculate the threshold
        all_train_predictions = {'human': [], 'llm': []}
        train_filenames = args.train_data_path.split(",")
        for filename in train_filenames:
            train_predictions = process_data(filename, args.llm_model)
            all_train_predictions['human'].extend(train_predictions['human'])
            all_train_predictions['llm'].extend(train_predictions['llm'])

        # Calculate threshold using all training data
        roc_auc, optimal_threshold, conf_matrix, precision, recall, f1, accuracy = get_roc_metrics(
            all_train_predictions['human'], all_train_predictions['llm']
        )
        logging.info(f"Optimal Threshold from Training Data: {optimal_threshold}")

        # Process and evaluate test data
        test_filenames = args.test_data_path.split(",")
        for filename in test_filenames:
            test_predictions = process_data(filename, args.llm_model)

            # Evaluate test predictions
            roc_auc_test, _, conf_matrix_test, precision_test, recall_test, f1_test, accuracy_test = get_roc_metrics_with_threshold(
                test_predictions['human'], test_predictions['llm'], optimal_threshold
            )

            # Save results
            output_results_path = filename.replace(".json", "_results_test.json")
            with open(output_results_path, "w") as f:
                json.dump({
                    "roc_auc": roc_auc_test,
                    "optimal_threshold": optimal_threshold,
                    "conf_matrix": conf_matrix_test,
                    "precision": precision_test,
                    "recall": recall_test,
                    "f1": f1_test,
                    "accuracy": accuracy_test,
                }, f, indent=4)
            logging.info(f"Saved results to {output_results_path}")


if __name__ == "__main__":
    # Argument parser for command-line inputs
    parser = argparse.ArgumentParser()
    parser.add_argument("--train_data_path", type=str, required=False,
                        help="Path to the training data. Multiple files can be separated by commas.")
    parser.add_argument("--test_data_path", type=str, required=True,
                        help="Path to the test data. Multiple files can be separated by commas.")
    parser.add_argument("--llm_model", default="gpt-4o-mini", type=str,
                        help="LLM model to use.", required=False)
    parser.add_argument("--threshold", default=False, type=bool,
                        help="setting threshold or not.", required=False)
    parser.add_argument("--threshold_value", default=0.9243697428995128, type=float,
                        help="setting threshold without training.", required=False)
    parser.add_argument("--seed", default=2023, type=int,
                        help="Random seed for reproducibility.")
    args = parser.parse_args()

    # Run the experiment
    experiment(args)