import json
from tqdm import tqdm
import rdflib
import requests
import random
from datetime import datetime

# SEED_PATH_LEN: Tune according the capacity of the endpoint
SEED_PATH_LEN = 4
# SEED_BATCHES: Should be high (over 300) for short path queries, and small (10) for long path queries
SEED_BATCHES = 10
# ENDPOINT_LIMIT: Should be high for short path queries, adjust according to the capacity of the endpoint
ENDPOINT_LIMIT = 5000
# QUERIES_PER_SEED: Set up to 1 for short path queries, 3 for long path queries (with length >= 5)
QUERIES_PER_SEED = 2
# P_EDGE: Can stay like this
P_EDGE = 0.9
# P_EDGE: Can stay like this
P_NODE = 0.3
# FINAL_QUERY_TIMEOUT: Can be set up higher for long path queries
FINAL_QUERY_TIMEOUT = 3
# P_START_END: Probability of instantiating the start or end of the path
P_START_END = 0.3


def generate_template(n_triples, start=1):
    where = ""
    for i in range(start, n_triples + 1):
        where += " ?o" + str(i - 1) + " ?p" + str(i) + " ?o" + str(i) + " . "
    return where


def get_seed_paths(path, endpoint_url, path_length):
    project = ["?p"+str(i) for i in range(1, path_length+1)]
    r = requests.get(endpoint_url,
                     params={'query': "SELECT DISTINCT " + ' '.join(project) + " WHERE { " + path + " } "
                                      "ORDER BY ASC(bif:rnd(2000000000)) " +
                                      "LIMIT " + str(ENDPOINT_LIMIT),
                             'format': 'json'})
    res = r.json()
    res = res["results"]["bindings"]
    return res


def instantiate_path(bindings, query, seed=False, factor=1, path_len=1):
    entities = []
    for (k, v) in bindings.items():
        # Instantiate predicate in path with very high probability
        if k.startswith("p") and random.random() < P_EDGE:
            query = query.replace("?" + k, "<" + bindings[k]['value'] + ">")
            entities.append(bindings[k]['value'])
        # Instantiate last node in the non-seed paths with some probability
        elif not seed and k == 'o'+str(path_len) and random.random() < P_START_END:
            if bindings[k]['type'] == 'uri':
                query = query.replace("?" + k, "<" + bindings[k]['value'] + ">")
                entities.append(bindings[k]['value'])
            else:
                query = query.replace("?" + k, '"' + bindings[k]['value'] + '"')
                entities.append(bindings[k]['value'])
        # Instantiate starting node in the non-seed paths with some probability
        elif not seed and k == 'o0' and random.random() < P_START_END:
            query = query.replace("?" + k, "<" + bindings[k]['value'] + ">")
            entities.append(bindings[k]['value'])
        # Instantiate intermediate node in path with random probability
        elif bindings[k]['type'] == 'uri' and random.random() < (P_NODE * factor):
            query = query.replace("?" + k, "<" + bindings[k]['value'] + ">")
            entities.append(bindings[k]['value'])
    return query, entities


def get_queries(graphfile, dataset_name, n_triples=1, n_queries=30000, endpoint_url=None, outfile=True, get_cardinality=True):
    now = datetime.now()

    # Determine length of seed path
    spl = min(SEED_PATH_LEN, n_triples)

    # Get candidate paths of length SEED_PATH_LEN
    print("Getting seed paths of length", spl)
    path = generate_template(spl, 1)
    res = []
    for i in tqdm(range(0, SEED_BATCHES)):
        res += get_seed_paths(path, endpoint_url, spl)

    print("Generating path queries ...")
    res = [x for n, x in enumerate(res) if res.index(x) == n]
    testdata = []
    #for i in range(0, n_queries):
    for i in tqdm(range(0, n_queries)):
        try:
            # Select a seed path to instantiate the first part of the path, and extend query
            j = random.randint(0, len(res) - 1)
            query_j, entities = instantiate_path(res[j], path, seed=True, factor=0.01)
            query_expansion = generate_template(n_triples, spl + 1)

            rj = requests.get(endpoint_url,
                              params={'query': "SELECT * WHERE { " + query_j + query_expansion + " } LIMIT 1000",
                                      'format': 'json'},
                              timeout=8)

            if rj.status_code == 200:
                qres = rj.json()
                qres = qres["results"]["bindings"]

                if not qres:
                    continue

                # Expand the query with new bindings to get the final query
                for k in range(0, QUERIES_PER_SEED):
                    n = random.randint(0, len(qres) - 1)
                    final_query, entities2 = instantiate_path(qres[n], query_j + query_expansion, factor=0.1, path_len=n_triples)

                    if not get_cardinality:
                        testdata.append({"query": "SELECT * WHERE { " + final_query + " }",
                                         "triples": [elem.strip().split() for elem in final_query.split(" .")[:-1]]})
                        continue

                    # Get cardinality of query
                    rn = requests.get(endpoint_url,
                                      params={'query': "SELECT COUNT(*) as ?res WHERE { " + final_query + " }",
                                              'format': 'json'},
                                      timeout=FINAL_QUERY_TIMEOUT)
                    if rn.status_code == 200:
                        qres2 = rn.json()
                        qres2 = qres2["results"]["bindings"]
                        datapoint = {"x": entities + entities2,
                                     "y": int(qres2[0]["res"]["value"]),
                                     "query": "SELECT * WHERE { " + final_query + " }",
                                     "triples": [elem.strip().split() for elem in final_query.split(" . ")[:-1]]}
                        testdata.append(datapoint)
                        #print(qres2[0]["res"]["value"], "SELECT * WHERE { " + final_query + " }")

        except requests.exceptions.ReadTimeout:
            pass

        if outfile and i % 100 == 0:
            with open(dataset_name + "_path_" + now.strftime('%Y-%m-%d_%H-%M-%S_') + str(n_triples) + ".json", "w") as fp:
                json.dump(testdata, fp)

    # Write output
    if outfile:
        with open(dataset_name + "_path_" + now.strftime('%Y-%m-%d_%H-%M-%S_') + str(n_triples) + ".json", "w") as fp:
            json.dump(testdata, fp)

    print("Done:", len(testdata))
    return testdata


if __name__ == "__main__":
    get_queries(None, "gcare-yago", n_triples=3, n_queries=6000,
                 endpoint_url="http://localhost:8896/sparql", outfile=True)

