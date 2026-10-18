# Book ordering and segmenting

Segmentación de *Calibán y la bruja* (Silvia Federici) en un PDF por capítulo.

## Qué hace

1. Extrae el EPUB de `source/`.
2. Une cada capítulo (portadilla + cuerpo cuando aplica).
3. Inserta cada nota al pie **junto al marcador** en el texto, entre corchetes `[…]`, en lugar de dejarlas al final.
4. Genera PDFs A4 tipografiados en `chapters/`.

## Uso

```bash
cd book-ordering-and-segmenting
python3 -m pip install -r requirements.txt
python3 scripts/segment_epub_to_pdfs.py
```

Los PDF quedan en `chapters/` y el índice en `chapters/MANIFEST.md`.
