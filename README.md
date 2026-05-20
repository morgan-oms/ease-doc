# EASE-Doc

Framework para **extração**, **avaliação** e **seleção** de esquemas NoSQL orientados a documentos. O EASE-Doc consome os artefatos gerados pela análise de U-Schema e estima o custo de alternativas de esquema (incluindo planos de duplicação) para apoiar decisões de modelagem em bancos documentais.

## Motivação

Em bancos NoSQL schemaless, o esquema lógico está implícito no código e nos padrões de acesso. Comparar alternativas de modelagem (normalização, embutimento, duplicação de dados) exige:

- identificar coleções, operações de leitura/escrita e planos de duplicação no código-fonte;
- estimar volumetria, tamanho de documentos e custo de I/O sob uma carga de trabalho;
- explorar combinações de duplicações de forma sistemática.

O EASE-Doc automatiza esse fluxo sobre a saída do pipeline [U-Schema Code Analysis](https://github.com/modelum/uschema-code-analysis), descrito em *Automated Extraction and Refactoring of NoSQL Schemas from Application Code* (Modelum / U-Schema).

## Arquitetura


| Módulo                       | Responsabilidade                                                                                                                                                                                                 |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Extrator** (`extractor/`)  | Carrega artefatos do U-Schema: coleções, duplicações, padrões de leitura/escrita, tamanhos BSON, volumetria e workload (`ease_doc.config.json`).                                                                 |
| **Avaliador** (`analyzer/`)  | Ajusta padrões e tamanhos de documento conforme duplicações ativas; interpreta referências no `dboSchemaModel.xmi`; estima o custo total de cada esquema candidato via `cost_evaluator` (Eqs. 1–8 do artigo).    |
| **Seletor** (`selector/`)    | Representação de esquemas candidatos (flags), geração de vizinhanças e busca local *hill climbing best-improvement* sobre o espaço de duplicações.                                                               |

## Pré-requisito obrigatório

> **Observação:** o EASE-Doc **somente funciona** se o repositório [uschema-code-analysis](https://github.com/modelum/uschema-code-analysis) estiver clonado na **raiz deste projeto**. Essa ferramenta é o componente principal do módulo extrator: todos os caminhos padrão dos loaders apontam para a pasta `outputs/` gerada pelo launcher Eclipse.

## Configuração inicial

### Requisitos

- **Python 3.10+**
- **U-Schema Code Analysis** (seção anterior)

### Passos

1. **Clone este repositório**
  ```bash
   git clone https://github.com/SEU_USUARIO/ease-doc.git
   cd ease-doc
  ```
2. **Adicione o U-Schema na raiz**
  ```bash
   git clone https://github.com/modelum/uschema-code-analysis.git
  ```
3. **Gere os artefatos** (Eclipse EMF + Run `Launcher.java`, conforme documentação do [repositório upstream](https://github.com/modelum/uschema-code-analysis)).
4. **Configure o cenário** em `ease_doc.config.json`:
  - `base_schema` — nome dado ao esquema base de referência;
  - `volume.collections` — contagem esperada de documentos por coleção;
  - `workload` — mix de leitura/escrita (`read_mix`, `write_mix`).

Após esses passos, prossiga para a seção [Execução](#execução).

## Execução

A execução padrão dispara o pipeline completo: extração via U-Schema → construção do avaliador → busca local (*hill climbing best-improvement*) → persistência dos resultados em CSV → relatório no terminal.

### Modo padrão (pipeline completo)

```bash
python main.py
```

Saída esperada:

- `easedoc_<base_schema>_path_R<α>_W<1−α>.csv` — trajeto da busca, uma linha por passo aceito (inclui `step`, `move`, `from_schema`, `to_schema`, `improvement`, `total_cost`, `read_cost`, `write_cost`).
- `easedoc_<base_schema>_final_R<α>_W<1−α>.csv` — linha final por perfil de volumetria, com o esquema selecionado, contadores e tempo de execução.
- Bloco textual no terminal com o **esquema selecionado**, custo total estimado, ReadCost, WriteCost, número de passos, candidatos avaliados e tempo total.

### Flags disponíveis

| Flag                     | Descrição                                                                                          |
| ------------------------ | -------------------------------------------------------------------------------------------------- |
| `-c, --config PATH`      | Arquivo de configuração alternativo (padrão: `ease_doc.config.json` na raiz).                      |
| `-f, --duplication-file` | `possibles-duplication.js` alternativo (padrão: saída do launcher U-Schema).                       |
| `-o, --output-dir PATH`  | Diretório de saída para os CSVs (padrão: `./`).                                                    |
| `--json`                 | Imprime a resposta final em JSON em vez do relatório textual.                                      |
| `--no-write`             | Não persiste CSVs; apenas imprime o resultado no terminal.                                         |
| `--diagnose`             | Não executa a busca; apenas imprime os artefatos extraídos (ver seção [Debug](#debug)).            |

Exemplos:

```bash
# Pipeline completo, salvando CSVs em ./results
python main.py -o results/

# Resposta final em JSON, sem gerar CSVs (útil para integração)
python main.py --json --no-write

# Usar config e plano de duplicação alternativos
python main.py -c configs/cenario_R10_W90.json -f outputs/dupes_v2.js
```

## Debug

O modo `--diagnose` permite inspecionar **tudo o que o Extrator carrega** antes de qualquer cálculo do modelo de custo. É a primeira ferramenta a usar quando:

- o launcher U-Schema gerou novos artefatos e você quer conferir o que foi reconhecido;
- a busca termina em um esquema inesperado e você quer auditar a entrada;
- o `ease_doc.config.json` está com volumetria ou workload suspeitos.

```bash
python main.py --diagnose
```

A saída lista, em ordem:

1. **Duplicações extraídas** (`D1, D2, ...`) lidas do `possibles-duplication.js`.
2. **Coleções base** identificadas no `noSQLSchemaModel.xmi`.
3. **Volumetria** ativa (perfil + contagem de documentos por coleção).
4. **Workload** ativo (`alpha_read`, `read_mix`, `write_mix`, `scenario_id`).
5. **Tamanho médio dos documentos** por coleção (em bytes, estimado via BSON).
6. **Padrões de leitura** por consulta (`Q1, Q2, ...`).
7. **Padrões de escrita** por operação (`U1, U2, ..., C1, C2, ...`), incluindo contagem de `reads`/`writes` por etapa.

### Pontos de inspeção rápidos

| Sintoma                                            | O que verificar                                                                  |
| -------------------------------------------------- | -------------------------------------------------------------------------------- |
| Nenhuma duplicação encontrada                      | Caminho do `possibles-duplication.js` e geração do launcher U-Schema.            |
| `total_cost` desproporcionalmente alto             | Volumetria (`volume.collections`) e tamanho médio dos documentos.                |
| Esquema final igual ao base (sem duplicações)      | `read_mix`/`write_mix` e `alpha_read` no `ease_doc.config.json`.                 |
| Coleções faltando nos padrões                      | Conferir `noSQLSchemaModel.xmi` e `dboSchemaModel.xmi` na pasta `outputs/`.      |

### Inspecionando o trajeto da busca

Para auditar a busca, abra o `easedoc_<base_schema>_path_R<α>_W<1−α>.csv` gerado pelo modo padrão: cada linha mostra o esquema corrente, a duplicação alterada (`move`), o ganho (`improvement`) e os custos parciais. A linha com `move == "START"` é o ponto de partida (sem duplicações ativas) e a última linha corresponde ao esquema retornado pelo Seletor.

## Referências

- [uschema-code-analysis](https://github.com/modelum/uschema-code-analysis) — extração de esquemas NoSQL e planos de duplicação a partir de código.
- [U-Schema](https://github.com/modelum/uschema) — metamodelo lógico utilizado pelo pipeline.

