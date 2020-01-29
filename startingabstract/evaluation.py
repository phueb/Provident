import pyprind
import torch
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from itertools import product


from startingabstract import config
from startingabstract.representation import make_representations_without_context
from startingabstract.representation import make_representations_with_context
from startingabstract.representation import make_output_representation


def calc_perplexity(model, criterion, prep):
    print(f'Calculating perplexity...')

    pp_sum = torch.tensor(0.0, requires_grad=False)
    num_batches = 0
    pbar = pyprind.ProgBar(prep.num_mbs, stream=1)

    for windows in prep.gen_windows():

        # to tensor
        x, y = np.split(windows, [prep.context_size], axis=1)
        inputs = torch.cuda.LongTensor(x)
        targets = torch.cuda.LongTensor(np.squeeze(y))

        # calc pp (using torch only, on GPU)
        logits = model(inputs)['logits']  # initial hidden state defaults to zero if not provided
        loss_batch = criterion(logits, targets).detach()  # detach to prevent saving complete graph for every sample
        pp_batch = torch.exp(loss_batch)  # need base e

        pbar.update()

        pp_sum += pp_batch
        num_batches += 1
    pp = pp_sum / num_batches
    return pp.item()


def update_pp_performance(performance, model, criterion, train_prep, test_prep):
    if not config.Global.train_pp:
        if config.Eval.num_test_docs > 0:
            test_pp = calc_perplexity(model, criterion, test_prep)
            performance['test_pp'].append(test_pp)
    else:
        if config.Eval.num_test_docs > 0:
            train_pp = calc_perplexity(model, criterion, train_prep)  # TODO cuda error
            test_pp = calc_perplexity(model, criterion, test_prep)
            performance['train_pp'].append(train_pp)
            performance['test_pp'].append(test_pp)
    return performance


def update_ba_performance(performance, model, train_prep, ba_scorer):

    for name in ba_scorer.probes_names:

        probe_store = ba_scorer.name2store[name]

        probe_reps_o = make_representations_with_context(model, probe_store.vocab_ids, train_prep)
        probe_reps_n = make_representations_without_context(model, probe_store.vocab_ids)

        probe_sims_o = cosine_similarity(probe_reps_o)
        probe_sims_n = cosine_similarity(probe_reps_n)

        performance.setdefault(f'ba_o_{name}', []).append(ba_scorer.calc_score(probe_sims_o, probe_store.gold_sims, 'ba'))
        performance.setdefault(f'ba_n_{name}', []).append(ba_scorer.calc_score(probe_sims_n, probe_store.gold_sims, 'ba'))

    return performance


def update_dp_performance(performance, model, train_prep, dp_scorer):  # TODO is this still useful?
    """
    calculate distance-to-prototype (aka dp):
    all divergences are relative to the prototype that best characterizes members belonging to name
    """
    for name in dp_scorer.probes_names:
        # collect dp for probes who tend to occur most frequently in some part of corpus
        for part in range(config.Eval.dp_num_parts):
            probes = dp_scorer.name2part2probes[name][part]
            qs = make_output_representation(model, probes, train_prep)

            # check predictions
            max_ids = np.argsort(qs.mean(axis=0))
            print(f'{name} predict:', [train_prep.store.types[i] for i in max_ids[-10:]])

            # dp
            performance.setdefault(f'dp_{name}_part{part}_js', []).append(dp_scorer.calc_dp(qs, name, metric='js'))

    return performance


def update_cs_performance(performance, model, train_prep, cs_scorer):  # TODO test
    """
    compute category-spread
    """
    for name in cs_scorer.probes_names:
        for cat1, cat2 in product(['NOUN'], cs_scorer.name2store[name].cats):
            ps = make_output_representation(model, cs_scorer.name2store[name].cat2probes[cat1], train_prep)
            qs = make_output_representation(model, cs_scorer.name2store[name].cat2probes[cat2], train_prep)

            performance.setdefault(f'cs_{name}_{cat1}_{cat2}_js', []).append(cs_scorer.calc_cs(ps, qs, metric='js'))
            print('Done')

    return performance


def get_weights(model):
    ih = model.rnn.weight_ih_l  # [hidden_size, input_size]
    hh = model.rnn.weight_hh_l  # [hidden_size, hidden_size]
    return {'ih': ih, 'hh': hh}