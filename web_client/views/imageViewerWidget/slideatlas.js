import { staticRoot } from 'girder/rest';

import ImageViewerWidget from './base';

var SlideAtlasImageViewerWidget = ImageViewerWidget.extend({
    initialize: function (settings) {
        if (!$('head #large_image-slideatlas-css').length) {
            $('head').prepend(
                $('<link>', {
                    id: 'large_image-slideatlas-css',
                    rel: 'stylesheet',
                    href: staticRoot + '/built/plugins/large_image/extra/slideatlas/sa.css'
                })
            );
        }

        $.when(
            ImageViewerWidget.prototype.initialize.call(this, settings),
            $.ajax({ // like $.getScript, but allow caching
                url: staticRoot + '/built/plugins/large_image/extra/slideatlas/sa-all.min.js',
                dataType: 'script',
                cache: true
            }))
            .done(() => this.render());
    },

    render: function () {
        // render can get clled multiple times
        if (this.viewer) {
            return this;
        }

        // If script or metadata isn't loaded, then abort
        if (!window.SA || !this.tileWidth || !this.tileHeight || this.deleted) {
            return this;
        }

        if (this.viewer) {
            // don't rerender the viewer
            return this;
        }

        // TODO: if a viewer already exists, do we render again?
        // SlideAtlas bundles its own version of jQuery, which should attach itself to "window.$" when it's sourced
        // The "this.$el" still uses the Girder version of jQuery, which will not have "saViewer" registered on it.
        var tileSource = {
            height: this.sizeY,
            width: this.sizeX,
            tileWidth: this.tileWidth,
            tileHeight: this.tileHeight,
            minLevel: 0,
            maxLevel: this.levels - 1,
            units: 'mm',
            spacing: [this.mm_x, this.mm_y],
            getTileUrl: (level, x, y, z) => {
                // Drop the "z" argument
                return this._getTileUrl(level, x, y);
            }
        };
        if (!this.mm_x) {
            // tileSource.units = 'pixels';
            tileSource.spacing = [1, 1];
        }
        window.SA.SAViewer(window.$(this.el), {
            zoomWidget: true,
            drawWidget: true,
            prefixUrl: staticRoot + '/built/plugins/large_image/extra/slideatlas/img/',
            tileSource: tileSource
        });
        this.viewer = this.el.saViewer;
        this.girderGui = new window.SAM.GirderAnnotationPanel(this.viewer.GetAnnotationLayer(), this.itemId);
        $(this.el).css({position: 'relative'});
        window.SA.SAFullScreenButton($(this.el))
          .css({'position': 'absolute', 'left': '2px', 'top': '2px'});

        this.trigger('g:imageRendered', this);

        return this;
    },

    destroy: function () {
        if (this.viewer) {
            window.$(this.el).saViewer('destroy');
            this.viewer = null;
        }
        this.deleted = true;
        ImageViewerWidget.prototype.destroy.call(this);
    }
});

export default SlideAtlasImageViewerWidget;
