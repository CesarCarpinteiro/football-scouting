"""
Importa clubes + plantéis completos da La Liga (Espanha) 24/25,
reutilizando a mesma lógica do import_ligapt.py (que já processa
as ligas portuguesas). Corre isto isoladamente para não repetir o
scraping já feito para Portugal.
"""

import time

import import_ligapt as il

EPOCAS = il.EPOCAS


def main():
    conn = il.ligar_base_dados()
    cursor = conn.cursor()

    saison_id_alvo = next(iter(EPOCAS.values()))

    info_liga = il.LIGAS["La Liga"]
    clubes = il.extrair_clubes_liga("La Liga", info_liga["url"], saison_id_alvo)

    il.log(f"Total de clubes La Liga a processar: {len(clubes)}")

    clubes_ok = 0
    for indice, clube in enumerate(clubes, start=1):
        try:
            clube_id = il.obter_ou_criar_clube(cursor, clube)
            conn.commit()

            il.log(f"=== CLUBE {indice}/{len(clubes)} === "
                   f"{clube['nome']} | ID BD: {clube_id}")

            il.processar_clube(cursor, clube_id, clube, EPOCAS)
            conn.commit()

            clubes_ok += 1
            il.log(f"Clube {clube['nome']} processado e guardado.")

        except Exception as erro:
            conn.rollback()
            il.log(f"[ERRO] {clube['nome']}: {erro}")

        time.sleep(il.TEMPO_ESPERA_ENTRE_CLUBES)

    cursor.close()
    conn.close()
    il.log(f"La Liga concluída: {clubes_ok}/{len(clubes)} clubes processados.")


if __name__ == "__main__":
    main()
