"""
Importa clubes + plantéis completos da Eliteserien (Noruega),
reutilizando a mesma lógica do import_ligapt.py (que já processa
as outras ligas). Corre isto isoladamente para não repetir o
scraping já feito para as outras ligas.

ATENÇÃO -- época: a Noruega joga por ano civil (a época não atravessa
dois anos como "24/25"), e o "saison_id" do Transfermarkt para esta
liga está desfasado um ano do que o site mostra: saison_id=2024
corresponde à página "Eliteserien 2025" (confirmado a 2026-09-28,
verificando o <title> da página com esse parâmetro). É por isto que
NÃO se usa o EPOCAS partilhado do import_ligapt.py (que mapeia
"2024/25" -> 2024 para as outras ligas, um significado diferente) --
aqui o mapa é próprio: guarda-se como época "2025" na nossa BD,
sem inventar uma notação "24/25" que não existe para esta liga.
"""

import time

import import_ligapt as il

EPOCAS_NORUEGA = {"2025": 2024}


def main():
    conn = il.ligar_base_dados()
    cursor = conn.cursor()

    saison_id_alvo = next(iter(EPOCAS_NORUEGA.values()))

    info_liga = il.LIGAS["Eliteserien"]
    clubes = il.extrair_clubes_liga("Eliteserien", info_liga["url"], saison_id_alvo)

    il.log(f"Total de clubes Eliteserien a processar: {len(clubes)}")

    clubes_ok = 0
    for indice, clube in enumerate(clubes, start=1):
        try:
            clube_id = il.obter_ou_criar_clube(cursor, clube)
            conn.commit()

            il.log(f"=== CLUBE {indice}/{len(clubes)} === "
                   f"{clube['nome']} | ID BD: {clube_id}")

            il.processar_clube(cursor, clube_id, clube, EPOCAS_NORUEGA)
            conn.commit()

            clubes_ok += 1
            il.log(f"Clube {clube['nome']} processado e guardado.")

        except Exception as erro:
            conn.rollback()
            il.log(f"[ERRO] {clube['nome']}: {erro}")

        time.sleep(il.TEMPO_ESPERA_ENTRE_CLUBES)

    cursor.close()
    conn.close()
    il.log(f"Eliteserien concluída: {clubes_ok}/{len(clubes)} clubes processados.")


if __name__ == "__main__":
    main()
