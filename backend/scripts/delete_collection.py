"""Borra la colección de películas de Qdrant. Destructivo, para desarrollo.

Desde la migración sparse (2026-08-03) `movies` es un ALIAS que apunta a
`movies_v2`, y `delete_collection` sobre un alias devuelve `False` sin borrar
nada — comprobado. Este script seguía imprimiendo "Collection deleted": mentía
sin romper, que es la peor forma de fallar en algo cuyo único trabajo es
destruir datos. Ahora resuelve el alias y reporta lo que de verdad ha pasado.
"""
import asyncio

from qdrant_client import AsyncQdrantClient

NAME = "movies"


async def main():
    client = AsyncQdrantClient("http://qdrant:6333")
    try:
        aliases = {a.alias_name: a.collection_name
                   for a in (await client.get_aliases()).aliases}
        target = aliases.get(NAME, NAME)
        if target != NAME:
            print(f"{NAME} es un alias de {target}; se borra la colección real")
        deleted = await client.delete_collection(target)
        print(f"borrada: {deleted}  ({target})")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
