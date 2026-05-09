# [Título Curto e Descritivo do Documento]

> **Resumo (2-3 linhas):** explique em linguagem natural o que este documento cobre, para qual cultura/região/produto se aplica, e o tipo de dado (zoneamento, recomendação de adubação, controle fitossanitário, etc.). Este resumo é lido pelo roteador de documentos antes de qualquer busca, então seja específico: mencione nomes de culturas, cultivares, regiões, produtos e o escopo temporal.

**Metadados**
- **Fonte:** [EMBRAPA / Jacto / laudo interno / manual técnico — nome e edição]
- **Autor/Responsável:** [nome ou instituição]
- **Data de publicação:** [AAAA-MM-DD]
- **Versão:** [ex.: 2024.1]
- **Abrangência geográfica:** [município / estado / bioma]
- **Cultura(s) cobertas:** [liste todas; ex.: soja, milho safrinha, algodão herbáceo]
- **Tipo de conteúdo:** [zoneamento climático / recomendação técnica / laudo / catálogo de cultivares / manual de equipamento / histórico]

---

## Glossário (se o documento usa siglas/códigos)

Liste TODAS as siglas, códigos de solo, grupos de ciclo e unidades usadas no corpo do documento. O agente usa esta seção para resolver ambiguidades sem precisar inferir.

| Sigla / Código | Significado completo | Contexto de uso |
|---|---|---|
| AD1 | Solo arenoso, classe 1 (água disponível ≤ 30 mm) | Zoneamento de plantio |
| GRUPO I | Ciclo precoce (até 115 dias) | Soja, milho |
| NC | Necessidade de calagem (t/ha) | Análise de solo |
| CTC | Capacidade de Troca Catiônica (cmolc/dm³) | Análise de solo |

---

## Índice de Conteúdo

- [Seção 1 — Assunto principal](#secao-1)
- [Seção 2 — Assunto secundário](#secao-2)
- [Fórmulas e Procedimentos de Cálculo](#formulas)
- [Tabelas de Referência](#tabelas)
- [Perguntas Frequentes](#faq)

---

<a id="secao-1"></a>
## 1. [CULTURA / TEMA — NOME COMPLETO EM MAIÚSCULAS]

> **Escopo desta seção:** uma frase dizendo exatamente o que está aqui. Ex.: "Períodos recomendados de plantio para a cultura X em sequeiro, por classe de solo e grupo de ciclo, com base no zoneamento agroclimático de 2024."

### 1.1 Subseção específica (ex.: Zoneamento de Plantio)

**Regra de leitura:** cada linha abaixo é um registro independente. NÃO agregue, some ou combine linhas diferentes. Se a pergunta for sobre contagem, conte linhas. Se for sobre duração, use os campos `início` e `fim` de UMA linha específica.

Cada registro segue o formato:
`- **Ciclo:** <grupo> | **Solo:** <classe> | **Período:** <DD/MM> a <DD/MM> | **Risco:** <N%>`

Registros:

- **Ciclo:** GRUPO I | **Solo:** AD1 | **Período:** 01/01 a 10/02 | **Risco:** 20%
- **Ciclo:** GRUPO I | **Solo:** AD1 | **Período:** 21/08 a 31/12 | **Risco:** 20%
- **Ciclo:** GRUPO II | **Solo:** AD3 | **Período:** 21/10 a 31/10 | **Risco:** 20%

**Contagem por recorte (pré-calculada para o agente):**

- GRUPO I + AD1 @ Risco 20%: **2 períodos** (01/01–10/02; 21/08–31/12)
- GRUPO II + AD3 @ Risco 20%: **1 período** (21/10–31/10)

> Pré-computar contagens e totais elimina cálculos errados pelo LLM. Inclua sempre que a pergunta esperada envolva "quantos períodos", "quantas janelas", "total de dias".

### 1.2 Subseção com dados tabulares

Para tabelas de referência (dados que o usuário cruza com os próprios), use Markdown table. NÃO use CSV inline.

| Classe de Solo | Água Disponível (mm) | Profundidade efetiva (cm) | Aptidão Soja |
|---|---|---|---|
| AD1 | ≤ 30 | 30 | Restrita |
| AD2 | 31 – 50 | 40 | Moderada |
| AD3 | 51 – 70 | 50 | Boa |

**Observações sobre a tabela:**
- A coluna "Aptidão Soja" aplica-se apenas à cultivar de ciclo precoce.
- Classes acima de AD6 não são contempladas neste zoneamento.

---

<a id="formulas"></a>
## 2. Fórmulas e Procedimentos de Cálculo

Apresente cada fórmula como um bloco isolado com: **nome**, **quando usar**, **variáveis** (com unidades), **fórmula**, **exemplo numérico completo**.

### 2.1 Necessidade de Calagem (NC)

**Quando usar:** análise de solo indica V% abaixo da saturação por bases desejada para a cultura alvo.

**Variáveis:**
- `V2` = saturação por bases desejada (%) — depende da cultura
- `V1` = saturação por bases atual (%) — vem da análise de solo
- `CTC` = capacidade de troca catiônica (cmolc/dm³) — da análise
- `PRNT` = poder relativo de neutralização total do calcário (%) — da embalagem
- `NC` = necessidade de calagem (t/ha)

**Fórmula:**

```
NC = (V2 − V1) × CTC / PRNT
```

**Exemplo numérico passo a passo:**

Dados: V2=70%, V1=45%, CTC=8 cmolc/dm³, PRNT=85%.

1. Diferença de saturação: 70 − 45 = 25
2. Produto com CTC: 25 × 8 = 200
3. Divisão por PRNT: 200 / 85 = **2,35 t/ha**

**Casos de borda:**
- Se `V1 ≥ V2`: NC = 0 (não calar).
- Se `PRNT = 0`: dado inválido, solicitar nova análise do calcário.

---

<a id="tabelas"></a>
## 3. Tabelas de Referência

### 3.1 V2 recomendado por cultura

| Cultura | V2 recomendado (%) |
|---|---|
| Soja | 70 |
| Milho | 65 |
| Algodão | 60 |
| Cana-de-açúcar | 50 |

---

<a id="faq"></a>
## 4. Perguntas Frequentes (Pré-respondidas)

Inclua esta seção quando houver perguntas típicas do usuário final. O agente pode citá-la diretamente, economizando chamadas de ferramenta.

### P: "Quantos períodos de plantio existem para soja GRUPO I em AD1 a 20% de risco?"

**R:** 2 períodos, somando 153 dias:
- 01/01 a 10/02 (41 dias)
- 21/08 a 31/12 (112 dias)

Ver seção 1.1.

### P: "Qual a NC para V1=45%, V2=70%, CTC=8, PRNT=85%?"

**R:** 2,35 t/ha. Ver seção 2.1.

---

## 5. Convenções Seguidas Neste Documento

Esta seção é lida pelo agente para evitar interpretações erradas.

- **Datas:** formato `DD/MM` (ano subentendido — aplicar ao ano agrícola vigente).
- **Períodos:** sempre inclusivos nos dois extremos (`01/01 a 10/02` inclui tanto 01/01 quanto 10/02).
- **Risco:** percentual de frustração climática esperada (risco=20% significa 80% de chance de sucesso).
- **Nomes de cultivar:** sempre em MAIÚSCULAS e entre aspas quando mencionados (ex.: "BRS 1010 IPRO").
- **Unidades:** SI sempre que possível; quando comercial (saca, @, alqueire), explicitar ao lado.
- **Números decimais:** vírgula como separador (padrão pt-BR).

---

## 6. Limitações e Fora de Escopo

Diga explicitamente o que este documento NÃO cobre, para o agente poder responder honestamente "não está neste documento".

- Não cobre: zoneamento para agricultura irrigada (ver documento `zoneamento_irrigado_2024.md`).
- Não cobre: cultivares transgênicas lançadas após 2024.
- Não cobre: recomendações de inseticidas — ver manual fitossanitário correspondente.

---

## Anexos (opcional)

Se houver laudos, fotos, gráficos ou planilhas de apoio, liste-os aqui com uma linha descritiva. Arquivos binários devem ser indexados separadamente.
