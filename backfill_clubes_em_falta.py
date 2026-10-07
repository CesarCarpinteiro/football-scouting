"""
Backfill pontual: descobre clubes da época 24/25 que ficaram de fora
da importação original (bug em extrair_clubes_liga, já corrigido em
import_ligapt.py -- a lista de clubes vinha da época "atual" do
Transfermarkt em vez da época 24/25 pedida), e importa-os agora com
a mesma lógica (clube + plantel completo) usada no resto do pipeline.

Só processa clubes cujo transfermarkt_id ainda não existe na BD --
os restantes ~32 clubes já corretamente importados não são tocados.
"""

import time

import import_ligapt as il

EPOCAS = il.EPOCAS


def main():
    conn = il.ligar_base_dados()
    cursor = conn.cursor()

    saison_id_alvo = next(iter(EPOCAS.values()))

    todos_os_clubes = []
    for nome_liga, info_liga in il.LIGAS.items():
        clubes_liga = il.extrair_clubes_liga(
            nome_liga, info_liga["url"], saison_id_alvo
        )
        todos_os_clubes.extend(clubes_liga)
        time.sleep(il.TEMPO_ESPERA_ENTRE_CLUBES)

    clubes_em_falta = []
    for clube in todos_os_clubes:
        cursor.execute(
            "SELECT id FROM clubes WHERE transfermarkt_id = %s",
            (clube["transfermarkt_id"],),
        )
        if not cursor.fetchone():
            clubes_em_falta.append(clube)

    il.log(f"Clubes em falta detetados: {len(clubes_em_falta)}")
    for c in clubes_em_falta:
        il.log(f"  - {c['nome']} ({c['liga']}) transfermarkt_id={c['transfermarkt_id']}")

    for indice, clube in enumerate(clubes_em_falta, start=1):
        try:
            clube_id = il.obter_ou_criar_clube(cursor, clube)
            conn.commit()

            il.log(f"=== CLUBE EM FALTA {indice}/{len(clubes_em_falta)} === "
                   f"{clube['nome']} ({clube['liga']}) | ID BD: {clube_id}")

            il.processar_clube(cursor, clube_id, clube, EPOCAS)
            conn.commit()

            il.log(f"Clube {clube['nome']} processado e guardado.")

        except Exception as erro:
            conn.rollback()
            il.log(f"[ERRO] {clube['nome']}: {erro}")

        time.sleep(il.TEMPO_ESPERA_ENTRE_CLUBES)

    cursor.close()
    conn.close()
    il.log("Backfill de clubes em falta concluído.")


if __name__ == "__main__":
    main()
