import os
import json
from torchblocks.metrics import Accuracy
from torchblocks.trainer import TextClassifierTrainer
from torchblocks.callback import TrainLogger
from torchblocks.processor import TextClassifierProcessor, InputExample
from torchblocks.utils import seed_everything, dict_to_text, build_argparse
from torchblocks.utils import prepare_device, get_checkpoints
from transformers import BertConfig, BertTokenizer, WEIGHTS_NAME
from torchblocks.models.nn import BertForSiameseModel

MODEL_CLASSES = {
    'bert': (BertConfig, BertForSiameseModel, BertTokenizer)
}


class AfqmcProcessor(TextClassifierProcessor):

    def get_labels(self):
        """See base class."""
        return ["0", "1"]

    def read_data(self, input_file):
        """Reads a json list file."""
        with open(input_file, "r") as f:
            reader = f.readlines()
            lines = []
            for line in reader:
                lines.append(json.loads(line.strip()))
            return lines

    def create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            guid = "%s-%s" % (set_type, i)
            text_a = line['sentence1']
            text_b = line['sentence2']
            label = str(line['label']) if set_type != 'test' else None
            examples.append(
                InputExample(guid=guid, texts=[[text_a, None], [text_b, None]], label=label))
        return examples


def main():
    args = build_argparse().parse_args()

    if args.model_name is None:
        args.model_name = args.model_path.split("/")[-1]
    args.output_dir = args.output_dir + '{}'.format(args.model_name)
    os.makedirs(args.output_dir, exist_ok=True)
    # logging
    prefix = "_".join([args.model_name, args.task_name])
    logger = TrainLogger(log_dir=args.output_dir, prefix=prefix)

    # device
    logger.info("initializing device")
    args.device, args.n_gpu = prepare_device(args.gpu, args.local_rank)
    seed_everything(args.seed)
    args.model_type = args.model_type.lower()
    config_class, model_class, tokenizer_class = MODEL_CLASSES[args.model_type]

    # data processor
    logger.info("initializing data processor")
    tokenizer = tokenizer_class.from_pretrained(args.model_path, do_lower_case=args.do_lower_case)
    processor = AfqmcProcessor(data_dir=args.data_dir, tokenizer=tokenizer, prefix=prefix, encode_mode='pair')
    label_list = processor.get_labels()
    num_labels = len(label_list)
    args.num_labels = num_labels

    # model
    logger.info("initializing model and config")
    config = config_class.from_pretrained(args.model_path, num_labels=num_labels,
                                          cache_dir=args.cache_dir if args.cache_dir else None)
    model = model_class.from_pretrained(args.model_path, config=config)
    model.to(args.device)

    # trainer
    logger.info("initializing traniner")
    trainer = TextClassifierTrainer(logger=logger, args=args, collate_fn=processor.collate_fn,
                                    input_keys=processor.get_input_keys(),
                                    metrics=[Accuracy()])
    # do train
    if args.do_train:
        train_dataset = processor.create_dataset(args.train_max_seq_length, 'train.json', 'train')
        eval_dataset = processor.create_dataset(args.eval_max_seq_length, 'dev.json', 'dev')
        trainer.train(model, train_dataset=train_dataset, eval_dataset=eval_dataset)
    # do eval
    if args.do_eval and args.local_rank in [-1, 0]:
        results = {}
        eval_dataset = processor.create_dataset(args.eval_max_seq_length, 'dev.json', 'dev')
        checkpoints = [args.output_dir]
        if args.eval_all_checkpoints or args.checkpoint_number > 0:
            checkpoints = get_checkpoints(args.output_dir, args.checkpoint_number, WEIGHTS_NAME)
        logger.info("Evaluate the following checkpoints: %s", checkpoints)
        for checkpoint in checkpoints:
            global_step = checkpoint.split("/")[-1].split("-")[-1]
            model = model_class.from_pretrained(checkpoint, config=config)
            model.to(args.device)
            trainer.evaluate(model, eval_dataset, save_preds=True, prefix=str(global_step))
            if global_step:
                result = {"{}_{}".format(global_step, k): v for k, v in trainer.records['result'].items()}
                results.update(result)
        output_eval_file = os.path.join(args.output_dir, "eval_results.txt")
        dict_to_text(output_eval_file, results)
    # do predict
    if args.do_predict:
        test_dataset = processor.create_dataset(args.eval_max_seq_length, 'test.json', 'test')
        if args.checkpoint_number == 0:
            raise ValueError("checkpoint number should > 0,but get %d", args.checkpoint_number)
        checkpoints = get_checkpoints(args.output_dir, args.checkpoint_number, WEIGHTS_NAME)
        for checkpoint in checkpoints:
            global_step = checkpoint.split("/")[-1].split("-")[-1]
            model = model_class.from_pretrained(checkpoint)
            model.to(args.device)
            trainer.predict(model, test_dataset=test_dataset, prefix=str(global_step))


if __name__ == "__main__":
    main()
