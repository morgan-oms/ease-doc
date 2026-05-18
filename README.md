# EASE-Doc

Framework para **extração**, **avaliação** e **seleção** de esquemas NoSQL orientados a documentos. O EASE-Doc consome os artefatos gerados pela análise de U-Schema e estima o custo de alternativas de esquema (incluindo planos de duplicação) para apoiar decisões de modelagem em bancos documentais.

## Motivação

Em bancos NoSQL schemaless, o esquema lógico está implícito no código e nos padrões de acesso. Comparar alternativas de modelagem (normalização, embutimento, duplicação de dados) exige:

- identificar coleções, operações de leitura/escrita e planos de duplicação no código-fonte;
- estimar volumetria, tamanho de documentos e custo de I/O sob uma carga de trabalho;
- explorar combinações de duplicações de forma sistemática.

O EASE-Doc automatiza esse fluxo sobre a saída do pipeline [U-Schema Code Analysis](https://github.com/modelum/uschema-code-analysis), descrito em *Automated Extraction and Refactoring of NoSQL Schemas from Application Code* (Modelum / U-Schema).

## Arquitetura


| Módulo                       | Responsabilidade                                                                                                                                 |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Extrator** (`extractor/`)  | Carrega artefatos do U-Schema: coleções, duplicações, padrões de leitura/escrita, tamanhos BSON, volumetria e workload (`ease_doc.config.json`). |
| **Avaliador** (`analyzer/`)  | Ajusta padrões e tamanhos de documento conforme duplicações ativas; interpreta referências no `dboSchemaModel.xmi`.                              |
| **Seletor** (`selector/`)    | Representação de esquemas candidatos (flags) e geração de vizinhanças para busca heurística.                                                     |
| **Orquestrador** (`main.py`) | Ponto de entrada; agrega variáveis de configuração e expõe diagnóstico básico.                                                                   |


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
3. **Gere os artefatos** (Eclipse + `Launcher.java`, conforme documentação do [repositório upstream](https://github.com/modelum/uschema-code-analysis)).
4. **Configure o cenário** em `ease_doc.config.json`:
  - `base_schema` — esquema base de referência;
  - `volume.collections` — contagem de documentos por coleção;
  - `workload` — mix de leitura/escrita (`read_mix`, `write_mix`).
5. **Execute o diagnóstico**
  ```bash
   python main.py
  ```
   Para listar duplicações em JSON:

## Referências

- [uschema-code-analysis](https://github.com/modelum/uschema-code-analysis) — extração de esquemas NoSQL e planos de duplicação a partir de código.
- [U-Schema](https://github.com/modelum/uschema) — metamodelo lógico utilizado pelo pipeline.

